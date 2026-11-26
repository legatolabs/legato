"""Whole-request policies. Each sees exactly the outgoing request text and the declared ledger command schema."""
import json
import os
import re
import urllib.request

OP={"type":"object","properties":{"op":{"type":"string","enum":["set","copy","add"]},
    "account":{"type":"integer","minimum":1,"maximum":12},
    "arg":{"type":"integer","minimum":0,"maximum":999}},
    "required":["op","account","arg"],"additionalProperties":False}
BATCH={"type":"object","properties":{"steps":{"type":"array","items":OP}},"required":["steps"],"additionalProperties":False}
SYSTEM=("You translate customer ledger adjustments into a tool's typed instructions. Preserve the order. "
        "account is the destination account number, 1 through 12. For set, arg is the new value. "
        "For add, arg is the increment. For copy, arg is the source account number. "
        "Examples: 'set account 5 to 705' => set,5,705. 'copy account 3 to account 8' => copy,8,3. "
        "'Add 6 to account 7' => add,7,6. The tool will compute the resulting balance. Never invent an account.")
RULES=[
    (re.compile(r"Set account (\d+) to (\d+)\."),"set",False),
    (re.compile(r"Copy account (\d+) to account (\d+)\."),"copy",True),
    (re.compile(r"Increase account (\d+) by (\d+)\."),"add",False)
]


def strings(step,wording):
    op,row,n=step; row+=1
    if op=="set":return [f"Set account {row} to {n}.",f"The new balance for account {row} is {n}.",
                         f"Assign a balance of {n} to account {row}."][wording]
    if op=="copy":return [f"Copy account {n+1} to account {row}.",
                          f"Give account {row} the current balance of account {n+1}.",
                          f"Use the value in account {n+1} as the new balance of account {row}."][wording]
    return [f"Increase account {row} by {n}.",f"Credit another {n} to account {row}.",
            f"Raise the balance of account {row} by {n}."][wording]


def registered(line):
    for pattern,kind,reverse in RULES:
        m=pattern.fullmatch(line)
        if m:
            a,b=map(int,m.groups())
            return {"op":kind,"account":b if reverse else a,"arg":a if reverse else b}
    return None


def ask(lines,multi):
    schema=BATCH if multi else OP
    system=SYSTEM+(" Return one object with a steps array, one entry per numbered line." if multi else " Return a single command object.")
    prompt="\n".join(f"{i+1}. {s}" for i,s in enumerate(lines)) if multi else lines[0]
    body={"model":"legato-overall","messages":[{"role":"system","content":system},{"role":"user","content":prompt}],
          "temperature":0,"seed":3297,"max_tokens":520 if multi else 75,"cache_prompt":False,
          "response_format":{"type":"json_schema","json_schema":{"name":"plan","strict":True,"schema":schema}}}
    req=urllib.request.Request("http://127.0.0.1:18714/v1/chat/completions",data=json.dumps(body).encode(),
                                headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=150) as r:
        c=json.load(r)["choices"][0]
    if c["finish_reason"]!="stop":raise ValueError("incomplete generation")
    result=json.loads(c["message"]["content"])
    values=result["steps"] if multi else [result]
    if len(values)!=len(lines):raise ValueError("wrong number of commands")
    for v in values:
        if v["account"] not in range(1,13) or (v["op"]=="copy" and v["arg"] not in range(1,13)):
            raise ValueError("outside document scope")
    return values


def plan(lines,mode):
    if mode=="batch":return ask(lines,True),1
    parsed=[registered(line) for line in lines]
    indices=[i for i,v in enumerate(parsed) if v is None]
    if indices:
        for i,guess in zip(indices,ask([lines[i] for i in indices],True)):
            parsed[i]=guess
    return parsed,int(bool(indices))
