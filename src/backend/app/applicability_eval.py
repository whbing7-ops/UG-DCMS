"""无数据库依赖的 Applicability 表达式校验与求值器。"""
from __future__ import annotations
from typing import Any

OPS = {"eq","ne","in","not_in","between","gte","lte","gt","lt","contains","exists"}

def evaluate(expression: dict | None, context: dict) -> bool:
    if not expression: return True
    if "all" in expression: return all(evaluate(x, context) for x in (expression.get("all") or []))
    if "any" in expression: return any(evaluate(x, context) for x in (expression.get("any") or []))
    if "not" in expression: return not evaluate(expression.get("not"), context)
    field=expression.get("field"); op=expression.get("op","eq"); expected=expression.get("value")
    if not field: raise ValueError("Applicability 条件缺少 field")
    actual=context.get(field)
    if op=="exists": return (field in context) == bool(expected if expected is not None else True)
    if actual is None: return False
    if op=="eq": return actual==expected
    if op=="ne": return actual!=expected
    if op=="in": return actual in (expected or [])
    if op=="not_in": return actual not in (expected or [])
    if op=="contains": return expected in actual if isinstance(actual,(str,list,tuple,set)) else False
    if op=="between":
        if not isinstance(expected,list) or len(expected)!=2: raise ValueError("between 的 value 必须是 [min,max]")
        return expected[0] <= actual <= expected[1]
    if op=="gte": return actual>=expected
    if op=="lte": return actual<=expected
    if op=="gt": return actual>expected
    if op=="lt": return actual<expected
    raise ValueError(f"不支持的 Applicability 运算符: {op}")

def validate_expression(expression: dict) -> None:
    if not isinstance(expression,dict): raise ValueError("Applicability expression 必须是 JSON 对象")
    if any(k in expression for k in ("all","any")):
        key="all" if "all" in expression else "any"
        if not isinstance(expression[key],list) or not expression[key]: raise ValueError(f"{key} 必须是非空数组")
        for x in expression[key]: validate_expression(x)
        return
    if "not" in expression:
        validate_expression(expression["not"]); return
    if not expression.get("field"): raise ValueError("Applicability 条件缺少 field")
    if expression.get("op","eq") not in OPS: raise ValueError("Applicability 运算符无效")
    if expression.get("op")=="between":
        v=expression.get("value")
        if not isinstance(v,list) or len(v)!=2: raise ValueError("between 的 value 必须是 [min,max]")
