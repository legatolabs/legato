"""A bounded document tool. Its input contains only a caller-owned workbook, deck and operation plan."""
import io
import json
import re
import sqlite3
import urllib.request
from openpyxl import load_workbook
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

OP={"type":"object","properties":{"write":{"type":"string","enum":["title","body","subtitle","table","chart","append"]},
    "slide":{"type":"integer","minimum":1,"maximum":12},"sql":{"type":"string"},"prefix":{"type":"string"},
    "new_title":{"type":"string"}},"required":["write","slide","sql","prefix","new_title"],"additionalProperties":False}
SCHEMA={"type":"object","properties":{"plans":{"type":"array","items":OP}},"required":["plans"],"additionalProperties":False}
SYSTEM=("You implement a list of document requests using a typed spreadsheet-to-slides tool. Return exactly one plan "
        "per request, preserving order. It runs one read-only SQLite SELECT per plan. Data(Region TEXT, Month TEXT, "
        "Revenue INTEGER, MonthIndex INTEGER) has one row for each region and month. Summary(A1 TEXT, B2 INTEGER) "
        "has one row with the source workbook summary cells. The input workbook, target deck and SQL tables are "
        "already bound. Region provided as REQUESTED_REGION must be bound as :region in SQL. The slide field is "
        "one-based. write title, body or subtitle replaces exactly that field with the first selected value on each "
        "row, joined by newlines. prefix is prepended once, exactly as written, empty unless needed. table expects "
        "two selected columns with aliases as headers. chart expects two columns (category, value), ordered by "
        "MonthIndex. append creates a new title-and-content slide from new_title and the first result column as "
        "body; slide is 1 there and ignored. new_title is empty unless appending. Aggregate with SUM and GROUP BY "
        "when needed. Preserve all other content. SQL must compute answers from the data; do not guess values.")

def model(lines):
    opts={"model":"legato-overall","messages":[{"role":"system","content":SYSTEM},
            {"role":"user","content":"\n".join(f"{i+1}. {line}" for i,line in enumerate(lines))}],
          "temperature":0,"seed":4129,"cache_prompt":False,"max_tokens":850,
          "response_format":{"type":"json_schema","json_schema":{"name":"plans","strict":True,"schema":SCHEMA}}}
    req=urllib.request.Request("http://127.0.0.1:18714/v1/chat/completions",data=json.dumps(opts).encode(),
                               headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=150) as r:
        c=json.load(r)["choices"][0]
    if c["finish_reason"]!="stop":raise ValueError("incomplete plan")
    plans=json.loads(c["message"]["content"])["plans"]
    if len(plans)!=len(lines):raise ValueError("command count")
    return plans


def workbook(blob):
    w=load_workbook(io.BytesIO(blob),read_only=True,data_only=True)
    try:
        rows=iter(w["Data"].values)
        headers=next(rows)
        if headers!=("Region","Month","Revenue"):raise ValueError("unrecognized workbook layout")
        data=list(rows)
        summary=(w["Summary"]["A1"].value,w["Summary"]["B2"].value)
    finally:w.close()
    return data,summary


def canonical(line,source,target,region_names):
    line=line.replace(source,"SOURCE.xlsx").replace(target,"TARGET.pptx")
    match=re.search(r"for the ([A-Za-z]+) region\b",line)
    region=match[1] if match and match[1] in region_names else None
    if region:line=line[:match.start(1)]+"REQUESTED_REGION"+line[match.end(1):]
    return line,region


class OfficeTool:
    def __init__(self,xlsx,pptx):
        rows,summary=workbook(xlsx)
        months={m:i for i,m in enumerate(dict.fromkeys(r[1] for r in rows))}
        self.regions={r[0] for r in rows}
        self.pres=Presentation(io.BytesIO(pptx))
        self.signature=(tuple((s.slide_layout.name,tuple((p.placeholder_format.idx,str(p.placeholder_format.type)) for p in s.placeholders))
                             for s in self.pres.slides),summary[1] is not None)
        self.db=sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE Data(Region TEXT,Month TEXT,Revenue INT,MonthIndex INT)")
        self.db.executemany("INSERT INTO Data VALUES(?,?,?,?)",[(*r,months[r[1]]) for r in rows])
        self.db.execute("CREATE TABLE Summary(A1 TEXT,B2 INT)")
        self.db.execute("INSERT INTO Summary VALUES(?,?)",summary)
        allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION}
        self.db.set_authorizer(lambda code,*a:sqlite3.SQLITE_OK if code in allowed else sqlite3.SQLITE_DENY)
        self.db.set_progress_handler(lambda:1,100000)

    def apply(self,p,region):
        cursor=self.db.execute(p["sql"],{"region":region})
        rows=cursor.fetchmany(101)
        if not rows or len(rows)>100:raise ValueError("result size")
        if any(v is None for r in rows for v in r):raise ValueError("no cell found")
        kind=p["write"];value=p["prefix"]+"\n".join(str(r[0]) for r in rows)
        if kind=="append":
            slide=self.pres.slides.add_slide(self.pres.slide_layouts[1])
            slide.shapes.title.text=p["new_title"];slide.placeholders[1].text=value
            return
        slide=self.pres.slides[p["slide"]-1]
        if kind=="title":slide.shapes.title.text=value
        elif kind in ("body","subtitle"):slide.placeholders[1].text=value
        elif kind=="table":
            if len(cursor.description)!=2:raise ValueError("table columns")
            tab=slide.shapes.add_table(len(rows)+1,2, Inches(.5), Inches(2), Inches(7), Inches(3)).table
            for i,row in enumerate([[v[0] for v in cursor.description]]+rows):
                for j,cell in enumerate(row):tab.cell(i,j).text=str(cell)
        elif kind=="chart":
            if len(cursor.description)!=2:raise ValueError("chart columns")
            data=CategoryChartData();data.categories=[str(r[0]) for r in rows]
            data.add_series(region or "Revenue",[float(r[1]) for r in rows])
            slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(.5),Inches(2),Inches(7),Inches(3),data)
        else:raise ValueError("unknown effect")

    def save(self):
        f=io.BytesIO();self.pres.save(f);return f.getvalue()
