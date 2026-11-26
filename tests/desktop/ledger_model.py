"""Optional local structured-model policy for the paired native ledger test."""
import json
import os
import urllib.request

SCHEMA={"type":"object","properties":{"operation":{"type":"string","enum":["set","copy","add"]},
        "account":{"type":"integer","minimum":1,"maximum":12},"operand":{"type":"integer","minimum":0,"maximum":999}},
        "required":["operation","account","operand"],"additionalProperties":False}
SYSTEM=("Extract one command for a ledger integration. account is the destination account number. "
        "For set, operand is the new three-digit balance. For copy, operand is the SOURCE account number. "
        "For add, operand is the amount to add. The integration reads the live ledger for arithmetic. "
        "Examples: 'Set account 3 to 614' -> set,3,614. 'Copy account 11 to account 4' -> copy,4,11. "
        "'Increase account 9 by 12' -> add,9,12. Return the object only.")


def infer(step,index,records):
    op,target,arg=step
    if op=="set": instruction=f"Set account {target+1} to {arg}."
    elif op=="copy":instruction=f"Copy account {arg+1} to account {target+1}."
    else:instruction=f"Increase account {target+1} by {arg}."
    request={"model":"legato-long-ledger","messages":[{"role":"system","content":SYSTEM},
             {"role":"user","content":instruction}],"temperature":0,"seed":3297,"max_tokens":80,
             "cache_prompt":False,"response_format":{"type":"json_schema","json_schema":{"name":"update",
                                                                                          "strict":True,"schema":SCHEMA}}}
    url="http://127.0.0.1:18713/v1/chat/completions"
    req=urllib.request.Request(url,data=json.dumps(request).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=40) as response:
        choice=json.load(response)["choices"][0]
    if choice["finish_reason"]!="stop":raise ValueError("incomplete model response")
    out=json.loads(choice["message"]["content"])
    new_op,row,n=out["operation"],out["account"]-1,out["operand"]
    if new_op=="copy":n-=1
    if not 0<=row<len(records) or (new_op=="copy" and not 0<=n<len(records)):
        raise ValueError("outside document scope")
    if path:=os.environ.get("LEDGER_MODEL_LOG"):
        with open(path,"a",encoding="utf-8") as f:
            f.write(json.dumps(dict(index=index,instruction=instruction,selected=out))+"\n")
    return new_op,row,n
