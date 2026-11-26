"""Eight developer-registered document operations. Rendering, file grades and source queries are separate."""
import json
import urllib.request

NAMES=["summary_title","summary_subtitle","highest_region","ranked_region_list","total_revenue_text","regional_totals_table","growth_slide","monthly_chart"]
KINDS=["copy_title","copy_total","top_region","top3_list","compute_total","region_table","next_steps","paste_chart"]
CATALOG={
 "summary_title":("title",1,"SELECT A1 FROM Summary","",""),
 "summary_subtitle":("subtitle",1,"SELECT B2 FROM Summary","",""),
 "highest_region":("title",2,"SELECT Region FROM Data GROUP BY Region ORDER BY SUM(Revenue) DESC LIMIT 1","",""),
 "ranked_region_list":("body",2,"SELECT Region FROM Data GROUP BY Region ORDER BY SUM(Revenue) DESC LIMIT 3","",""),
 "total_revenue_text":("body",2,"SELECT SUM(Revenue) FROM Data","Total: ",""),
 "regional_totals_table":("table",2,"SELECT Region AS Region, SUM(Revenue) AS Total FROM Data GROUP BY Region","",""),
 "growth_slide":("append",1,"SELECT Region FROM Data GROUP BY Region ORDER BY SUM(Revenue) LIMIT 1","Grow ","Next Steps"),
 "monthly_chart":("chart",3,"SELECT Month,Revenue FROM Data WHERE Region=:region ORDER BY MonthIndex","","")}
TEXT={
 "summary_title":"put the workbook's summary heading in the first slide title",
 "summary_subtitle":"put the summary total in the first slide subtitle",
 "highest_region":"title the second slide with the highest grossing region",
 "ranked_region_list":"list the top three regions, highest first, in the second slide body",
 "total_revenue_text":"write the grand total with the Total: label in the second slide body",
 "regional_totals_table":"put each region and its revenue total in a table on slide two",
 "growth_slide":"append a Next Steps slide about growing the lowest grossing region",
 "monthly_chart":"add an ordered monthly revenue chart for the named region to slide three",
 "clarify":"the request is unresolved, forbids submission, or none of the operations applies"}

def plan(name):
    if name not in CATALOG:raise ValueError("clarification required")
    w,s,q,p,n=CATALOG[name]
    return dict(write=w,slide=s,sql=q,prefix=p,new_title=n)


def model(lines):
    enum=NAMES+["clarify"]
    schema={"type":"object","properties":{"tools":{"type":"array","items":{"type":"string","enum":enum}}},
            "required":["tools"],"additionalProperties":False}
    sys="Choose one registered operation per instruction, in the supplied order.\n"+"\n".join(k+" means "+v for k,v in TEXT.items())
    body={"model":"legato-overall","messages":[{"role":"system","content":sys},{"role":"user","content":"\n".join(f"{i+1}. {s}" for i,s in enumerate(lines))}],
          "temperature":0,"seed":3571,"cache_prompt":False,"max_tokens":100,"response_format":{"type":"json_schema","json_schema":{"name":"dispatch","strict":True,"schema":schema}}}
    req=urllib.request.Request("http://127.0.0.1:18714/v1/chat/completions",data=json.dumps(body).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=120) as r:c=json.load(r)["choices"][0]
    if c["finish_reason"]!="stop":raise ValueError("incomplete response")
    names=json.loads(c["message"]["content"])["tools"]
    if len(names)!=len(lines):raise ValueError("wrong number of operations")
    return names
