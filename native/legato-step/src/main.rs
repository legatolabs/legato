//! legato-step: Legato's in-sandbox helper, native. It does what the Python HELPER in `legato/env.py` does, with
//! the same JSON in and out:
//!
//!   legato-step '{"cmds": [[argv...], ...], "sleep": 0.0, "rule": {...}, "shot": true}'
//!   -> {"act_s": .., "settle_s": .., "capped": .., "grabs": .., "png": "<base64>", "impl": "rust"}
//!
//! It runs the commands, waits until the screen is quiet by the same stop rule (`legato.settle.quiet_enough`) and
//! returns the settled frame. What makes it faster than the Python helper:
//! - no interpreter start;
//! - one X connection for the whole call, and each check reads the framebuffer through MIT-SHM (GetImage when the
//!   server can't share memory) and hashes it in process, instead of running `xwd`;
//! - the PNG is encoded in process instead of by `scrot`, and speculatively: once a frame has been seen twice, it is
//!   encoded on a second thread while the wait sits out its quiet time, so the settled frame is usually ready when
//!   the wait ends.

use std::io::Write;
use std::process::{Command, ExitCode, Stdio};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use base64::Engine as _;
use serde::Deserialize;
use x11rb::connection::Connection;
use x11rb::protocol::shm::{self, ConnectionExt as _};
use x11rb::protocol::xproto::{ConnectionExt as _, ImageFormat, ImageOrder, Window};
use x11rb::rust_connection::RustConnection;

const VERSION: &str = env!("CARGO_PKG_VERSION");

fn d_floor() -> f64 {
    0.12
}
fn d_quiet() -> f64 {
    0.35
}
fn d_min_same() -> f64 {
    3.0
}
fn d_cap() -> f64 {
    2.0
}
fn d_poll() -> f64 {
    0.03
}

#[derive(Deserialize)]
struct Rule {
    #[serde(default = "d_floor")]
    floor: f64,
    #[serde(default = "d_quiet")]
    quiet: f64,
    #[serde(default = "d_min_same")]
    min_same: f64,
    #[serde(default = "d_cap")]
    cap: f64,
    #[serde(default = "d_poll")]
    poll: f64,
}

#[derive(Deserialize)]
struct Spec {
    #[serde(default)]
    cmds: Vec<Vec<String>>,
    #[serde(default)]
    sleep: Option<f64>,
    rule: Rule,
    #[serde(default)]
    shot: bool,
}

/// The stop test, as `legato.settle.quiet_enough`.
fn quiet_enough(now: f64, t0: f64, t_change: f64, same: f64, r: &Rule) -> bool {
    now - t0 >= r.floor && now - t_change >= r.quiet && same >= r.min_same
}

/// How the server lays out the root window's pixels.
#[derive(Clone, Copy)]
struct Fmt {
    w: usize,
    h: usize,
    bpp: usize,
    stride: usize,
    lsb: bool,
    masks: (u32, u32, u32),
}

impl Fmt {
    fn len(&self) -> usize {
        self.stride * self.h
    }
}

/// A frame as RGB8, for 24- and 32-bit pixels; None for other formats.
fn to_rgb(data: &[u8], f: Fmt) -> Option<Vec<u8>> {
    let bytes = match f.bpp {
        32 => 4,
        24 => 3,
        _ => return None,
    };
    if data.len() < f.len() {
        return None;
    }
    let ch = |m: u32| -> (u32, u32) {
        let sh = m.trailing_zeros().min(31);
        (sh, (m >> sh).max(1))
    };
    let (r, g, b) = (ch(f.masks.0), ch(f.masks.1), ch(f.masks.2));
    let bgrx = f.lsb && bytes == 4 && f.masks == (0xff0000, 0x00ff00, 0x0000ff); // the usual Xvfb layout
    let mut out = vec![0u8; f.w * f.h * 3];
    for y in 0..f.h {
        let row = &data[y * f.stride..y * f.stride + f.w * bytes];
        let dst = &mut out[y * f.w * 3..(y + 1) * f.w * 3];
        if bgrx {
            for (d, s) in dst.chunks_exact_mut(3).zip(row.chunks_exact(4)) {
                d[0] = s[2];
                d[1] = s[1];
                d[2] = s[0];
            }
            continue;
        }
        for (d, s) in dst.chunks_exact_mut(3).zip(row.chunks_exact(bytes)) {
            let mut px = 0u32;
            for i in 0..bytes {
                let k = if f.lsb { i } else { bytes - 1 - i };
                px |= (s[k] as u32) << (8 * i);
            }
            let scale = |(sh, max): (u32, u32)| (((px >> sh) & max) * 255 / max) as u8;
            d[0] = scale(r);
            d[1] = scale(g);
            d[2] = scale(b);
        }
    }
    Some(out)
}

/// PNG at zlib's default level with the Up filter: about the size scrot makes (smaller on most screens), in about
/// two thirds of its time. The fast levels encode 2-3x quicker but make files 2.5x larger, and every byte goes to
/// the client and on to the model.
fn encode_png(data: &[u8], f: Fmt) -> Option<Vec<u8>> {
    let rgb = to_rgb(data, f)?;
    let mut out = Vec::with_capacity(rgb.len() / 8);
    {
        let mut e = png::Encoder::new(&mut out, f.w as u32, f.h as u32);
        e.set_color(png::ColorType::Rgb);
        e.set_depth(png::BitDepth::Eight);
        e.set_compression(png::Compression::Default);
        e.set_filter(png::FilterType::Up);
        let mut wr = e.write_header().ok()?;
        wr.write_image_data(&rgb).ok()?;
    }
    Some(out)
}

struct Shm {
    seg: shm::Seg,
    addr: *mut u8,
    len: usize,
}

struct Screen {
    conn: RustConnection,
    root: Window,
    fmt: Fmt,
    shm: Option<Shm>,
    buf: Vec<u8>,
    in_shm: bool, // the last frame is in the shm segment (else in buf)
}

impl Screen {
    fn open() -> Option<Screen> {
        let (conn, n) = x11rb::connect(None).ok()?;
        let setup = conn.setup();
        let s = &setup.roots[n];
        let pf = setup.pixmap_formats.iter().find(|f| f.depth == s.root_depth)?;
        let (w, h, bpp) = (s.width_in_pixels as usize, s.height_in_pixels as usize, pf.bits_per_pixel as usize);
        let pad = (pf.scanline_pad as usize).max(8);
        let masks = s
            .allowed_depths
            .iter()
            .flat_map(|d| d.visuals.iter())
            .find(|v| v.visual_id == s.root_visual)
            .map(|v| (v.red_mask, v.green_mask, v.blue_mask))
            .unwrap_or((0xff0000, 0x00ff00, 0x0000ff));
        let fmt = Fmt {
            w,
            h,
            bpp,
            stride: (w * bpp).div_ceil(pad) * pad / 8,
            lsb: setup.image_byte_order == ImageOrder::LSB_FIRST,
            masks,
        };
        let root = s.root;
        let mut sc = Screen { conn, root, fmt, shm: None, buf: Vec::new(), in_shm: false };
        sc.shm = sc.attach_shm();
        Some(sc)
    }

    /// A shared-memory segment the X server writes frames into. None when the server has no MIT-SHM or cannot
    /// reach our segment (another IPC namespace, another user): then each frame comes over the socket.
    fn attach_shm(&self) -> Option<Shm> {
        if std::env::var_os("LEGATO_NO_SHM").is_some() {
            return None;
        }
        self.conn.shm_query_version().ok()?.reply().ok()?;
        let len = self.fmt.len();
        // SAFETY: plain SysV shm calls; the mapping lives until Drop detaches it.
        unsafe {
            let id = libc::shmget(libc::IPC_PRIVATE, len, libc::IPC_CREAT | 0o600);
            if id < 0 {
                return None;
            }
            let addr = libc::shmat(id, std::ptr::null(), 0);
            if addr as isize == -1 {
                libc::shmctl(id, libc::IPC_RMID, std::ptr::null_mut());
                return None;
            }
            let seg = self.conn.generate_id().ok().and_then(|seg| {
                self.conn.shm_attach(seg, id as u32, false).ok()?.check().ok()?;
                Some(seg)
            });
            // freed once both sides detach, so a crash leaks nothing
            libc::shmctl(id, libc::IPC_RMID, std::ptr::null_mut());
            match seg {
                Some(seg) => Some(Shm { seg, addr: addr as *mut u8, len }),
                None => {
                    libc::shmdt(addr);
                    None
                }
            }
        }
    }

    /// Grab the root window's pixels into the shm segment or `buf`. False if the server refused.
    fn grab(&mut self) -> bool {
        let (w, h, root) = (self.fmt.w as u16, self.fmt.h as u16, self.root);
        if let Some(s) = &self.shm {
            let ok = self
                .conn
                .shm_get_image(root, 0, 0, w, h, !0, ImageFormat::Z_PIXMAP.into(), s.seg, 0)
                .ok()
                .and_then(|c| c.reply().ok())
                .is_some();
            if ok {
                self.in_shm = true;
                return true;
            }
            self.shm = None; // fall back to the socket for the rest of this call
        }
        match self.conn.get_image(ImageFormat::Z_PIXMAP, root, 0, 0, w, h, !0).ok().and_then(|c| c.reply().ok()) {
            Some(r) => {
                self.buf = r.data;
                self.in_shm = false;
                true
            }
            None => {
                self.buf.clear();
                false
            }
        }
    }

    /// The last grabbed frame.
    fn frame(&self) -> &[u8] {
        match (&self.shm, self.in_shm) {
            // SAFETY: the segment is mapped for `len` bytes until Drop; the server has finished writing it once
            // the GetImage reply arrived.
            (Some(s), true) => unsafe { std::slice::from_raw_parts(s.addr, s.len) },
            _ => &self.buf,
        }
    }
}

impl Drop for Screen {
    fn drop(&mut self) {
        if let Some(s) = self.shm.take() {
            let _ = self.conn.shm_detach(s.seg);
            let _ = self.conn.flush();
            // SAFETY: the address came from shmat and nothing borrows the frame any more.
            unsafe {
                libc::shmdt(s.addr as *const libc::c_void);
            }
        }
    }
}

/// Grab and hash. Without a display every grab hashes the same, as the Python helper's failed `xwd` does.
fn check(sc: &mut Option<Screen>) -> u64 {
    match sc.as_mut() {
        Some(s) => {
            if s.grab() {
                xxhash_rust::xxh3::xxh3_64(s.frame())
            } else {
                xxhash_rust::xxh3::xxh3_64(&[])
            }
        }
        None => xxhash_rust::xxh3::xxh3_64(&[]),
    }
}

fn scrot_png() -> Option<Vec<u8>> {
    let path = "/dev/shm/legato_obs.png";
    Command::new("scrot").args(["-o", path]).stdout(Stdio::null()).stderr(Stdio::null()).status().ok()?;
    std::fs::read(path).ok()
}

fn run_cmd(argv: &[String]) -> Result<(), String> {
    let Some((prog, args)) = argv.split_first() else { return Ok(()) };
    if prog == "sleep" && args.len() == 1 {
        if let Ok(s) = args[0].parse::<f64>() {
            std::thread::sleep(Duration::from_secs_f64(s.max(0.0)));
            return Ok(());
        }
    }
    Command::new(prog)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|_| ())
        .map_err(|e| format!("{prog}: {e}"))
}

/// One PNG encode at a time, for the frame with hash `of`.
struct Pending {
    of: u64,
    job: JoinHandle<Option<Vec<u8>>>,
}

fn main() -> ExitCode {
    let arg = std::env::args().nth(1).unwrap_or_default();
    if arg == "--version" {
        println!("legato-step {VERSION}");
        return ExitCode::SUCCESS;
    }
    let spec: Spec = match serde_json::from_str(&arg) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("legato-step: bad spec: {e}");
            return ExitCode::from(2);
        }
    };
    let clock = Instant::now();
    let now = || clock.elapsed().as_secs_f64();
    let mut screen = Screen::open(); // before the action, so the connection is ready when the wait starts
    let speculate = spec.shot && std::env::var_os("LEGATO_NO_SPECULATE").is_none();

    let t0 = now();
    for cmd in &spec.cmds {
        if let Err(e) = run_cmd(cmd) {
            eprintln!("legato-step: {e}");
            return ExitCode::from(3);
        }
    }
    if let Some(s) = spec.sleep.filter(|s| *s > 0.0) {
        std::thread::sleep(Duration::from_secs_f64(s));
    }
    let t1 = now();
    let r = &spec.rule;

    let mut pending: Option<Pending> = None;
    let mut last = check(&mut screen);
    let mut t_change = now();
    let mut same = 1.0;
    let mut capped = false;
    let mut grabs = 1u32;
    loop {
        if now() - t1 >= r.cap {
            capped = true;
            break;
        }
        std::thread::sleep(Duration::from_secs_f64(r.poll.max(0.0)));
        let d = check(&mut screen);
        grabs += 1;
        let t = now();
        if d != last {
            last = d;
            t_change = t;
            same = 1.0;
        } else {
            same += 1.0;
        }
        if quiet_enough(t, t1, t_change, same, r) {
            break;
        }
        // the frame has held for one check: encode it now, while the wait sits out its quiet time
        let idle = pending.as_ref().is_none_or(|p| p.job.is_finished());
        if speculate && same == 2.0 && idle && pending.as_ref().is_none_or(|p| p.of != d) {
            if let Some(sc) = screen.as_ref().filter(|s| !s.frame().is_empty()) {
                let (data, fmt) = (sc.frame().to_vec(), sc.fmt);
                pending = Some(Pending { of: d, job: std::thread::spawn(move || encode_png(&data, fmt)) });
            }
        }
    }
    let settle_s = now() - t1;

    let mut res = serde_json::json!({
        "act_s": t1 - t0, "settle_s": settle_s, "capped": capped, "grabs": grabs, "impl": "rust",
    });
    if spec.shot {
        // a settled wait ends on a frame that has held for `quiet` s: send that one; after a cap, grab anew
        let fin = if capped { check(&mut screen) } else { last };
        let early = pending.filter(|p| p.of == fin).and_then(|p| p.job.join().ok().flatten());
        let png = early
            .or_else(|| screen.as_ref().and_then(|s| encode_png(s.frame(), s.fmt)))
            .or_else(scrot_png);
        if let Some(p) = png {
            res["png"] = base64::engine::general_purpose::STANDARD.encode(p).into();
        }
    }
    drop(screen);
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{res}");
    ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rule(floor: f64, quiet: f64, min_same: f64) -> Rule {
        Rule { floor, quiet, min_same, cap: 2.0, poll: 0.03 }
    }

    #[test]
    fn stops_only_when_floor_quiet_and_repeats_are_all_met() {
        let r = rule(0.12, 0.35, 3.0);
        assert!(quiet_enough(0.5, 0.0, 0.1, 3.0, &r));
        assert!(!quiet_enough(0.1, 0.0, -1.0, 9.0, &r)); // under the floor
        assert!(!quiet_enough(0.5, 0.0, 0.2, 9.0, &r)); // changed 0.3 s ago
        assert!(!quiet_enough(0.5, 0.0, 0.0, 2.0, &r)); // seen twice
    }

    fn fmt(w: usize, h: usize, bpp: usize, lsb: bool, masks: (u32, u32, u32)) -> Fmt {
        Fmt { w, h, bpp, stride: w * bpp / 8, lsb, masks }
    }

    #[test]
    fn bgrx_becomes_rgb() {
        let f = fmt(2, 1, 32, true, (0xff0000, 0x00ff00, 0x0000ff));
        assert_eq!(to_rgb(&[3, 2, 1, 0, 30, 20, 10, 0], f).unwrap(), vec![1, 2, 3, 10, 20, 30]);
    }

    #[test]
    fn other_masks_and_byte_order() {
        let f = fmt(1, 1, 32, false, (0x0000ff, 0x00ff00, 0xff0000)); // MSB first, RGB in the low bytes reversed
        assert_eq!(to_rgb(&[0, 30, 20, 10], f).unwrap(), vec![10, 20, 30]);
        let f = fmt(1, 1, 24, true, (0xff0000, 0x00ff00, 0x0000ff));
        assert_eq!(to_rgb(&[3, 2, 1], f).unwrap(), vec![1, 2, 3]);
        assert!(to_rgb(&[0, 0], fmt(1, 1, 16, true, (0xf800, 0x07e0, 0x001f))).is_none());
        assert!(to_rgb(&[0, 0], fmt(1, 1, 32, true, (0xff0000, 0x00ff00, 0x0000ff))).is_none()); // short frame
    }

    #[test]
    fn png_round_trips() {
        let f = fmt(2, 2, 32, true, (0xff0000, 0x00ff00, 0x0000ff));
        let p = encode_png(&[1, 2, 3, 0, 4, 5, 6, 0, 7, 8, 9, 0, 10, 11, 12, 0], f).unwrap();
        let mut r = png::Decoder::new(std::io::Cursor::new(p)).read_info().unwrap();
        let mut buf = vec![0; r.output_buffer_size()];
        r.next_frame(&mut buf).unwrap();
        assert_eq!(buf, vec![3, 2, 1, 6, 5, 4, 9, 8, 7, 12, 11, 10]);
    }
}
