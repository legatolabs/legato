# Desktop fork benchmark, E2a: 20261102T035459Z-290

| task | boot s | replay to fork s | boot+replay s | snapshot s | restore s (1) | restore p50 / p95 (fan-out) | fan-out wall s | screen same | files same | fork passes | orig passes | idle forks untouched | finishing forks pass |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| f_calc_insert_column_0 | 4.8 | 2.6 | 7.4 | 2.2 | 0.9 | 1.8 / 1.9 | 2.7 | 9/9 | 9/9 | True | None | 4/4 | 4/4 |
| f_writer_change_word_0 | 4.1 | 1.8 | 6.0 | 3.1 | 0.8 | 1.7 / 1.8 | 3.6 | 9/9 | 9/9 | True | None | 4/4 | 3/4 |
| f_text_indent_line_0 | 3.1 | 0.9 | 4.0 | 1.9 | 0.7 | 1.7 / 1.7 | 3.6 | 9/9 | 9/9 | True | None | 4/4 | 4/4 |
| f_files_move_file_0 | 2.7 | 1.2 | 3.9 | 1.9 | 0.7 | 1.7 / 1.7 | 2.5 | 9/9 | 9/9 | True | None | 4/4 | 4/4 |

exec round trip (included in every ready/restore time above): median 0.16s
