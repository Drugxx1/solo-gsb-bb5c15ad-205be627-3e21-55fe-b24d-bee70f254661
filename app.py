"""编织说明逐行针数校验 —— Flask 后端。

POST /api/validate        单尺码校验
  请求: {"cast_on": 12, "pattern": "(k2tog yo)*6\np*12\n..."}
  响应: {
    "ok": true/false,
    "rows": [{"line", "instruction", "in", "consume", "produce", "out"}, ...],
    "error": null | {"line", "type": "parse"|"count"|"cast_on", "message",
                     "char"?, "available"?, "consume"?, "diff"?},
    "final": 全部通过时的最终针数
  }

POST /api/validate-sizes  多尺码校验（可附带线材用量估算）
  请求: {"sizes": [{"name": "S", "cast_on": 12}, ...], "pattern": "...",
         "yarn": {"k": 0.5, "p": 0.5, "yo": 0.2, "k2tog": 1.0,
                  "waste_percent": 10, "grams_per_ball": 50}}
  响应: {
    "ok": true/false,
    "sizes": [{"name", "cast_on", "ok", "rows", "error", "final", "yarn"?}, ...],
    "suggestion": null | {"line", "char", "old", "new", "instruction", "produces"}
  }
  提供 yarn 时，每行额外返回该尺码截至该行的累计克重 "weight"，尺码结果
  额外包含:
    "yarn": {"raw_total", "with_waste", "balls", "partial", "complete"}
  尺码列表或线材参数非法时:
    {"ok": false, "sizes": [], "error": {"type": "sizes"|"yarn", ...}}

POST /api/move-size       尺码上移/下移，并同步重排指令中的按尺码倍数
  请求: {"sizes": [...], "pattern": "...", "index": 0, "direction": "up"|"down"}
  成功: {"ok": true, "sizes": 重排后的尺码列表, "pattern": 重排后的指令原文}
    每行的 *{a|b|...} 按新尺码顺序重排（嵌套重复与同一行多处均处理），普通
    *n 与其他原文保持不变。
  失败: {"ok": false, "error": {"type": "sizes"|"move"|"reorder", ...}}
    任一处按尺码倍数项数或数值不合法时整次移动不生效，reorder 错误带原文
    行号 "line" 与字符位置 "char"（均从 1 计）。

POST /api/preview-row      单行针位预览（不展开重复，十亿次重复可直接看尾段）
  请求: {"sizes": [...], "pattern": "...", "line": 1, "size": 0, "start": 1}
    line 为图解原文行号（1 起），size 为所选尺码下标（0 起，默认 0），
    start 为起始操作序号（1 起，默认 1），每页最多 50 个实际针法。
  成功: {"ok": true, "line", "instruction", "size", "size_name",
         "total_stitches", "total_consume", "total_produce", "page_size",
         "start", "end", "has_prev", "has_next", "prev_start", "next_start",
         "stitches": [{"seq", "stitch", "in_start", "in_end", "out_start",
                       "out_end", "insert_after"}, ...]}
    入针范围为该行行内 1 起编号；零入针（挂线 yo）的 in_start/in_end 为
    null，insert_after 标明插入在已消耗第几针之后（0=第 1 针之前）。
  失败: {"ok": false, "error": {"type": "sizes"|"line"|"size"|"start"|"parse",...}}
    非法行沿用现有解析错误（parse，带原文行号 line 与字符位置 char），
    不返回任何部分预览。

逐行推算规则：每个尺码独立地以上一行的产出作为下一行的可用针数；某尺码
消耗与可用针数不一致（不足或超出）时，仅该尺码停止并记录行号与差额，
其余尺码继续推算。指令本身无法解析（含 *{a|b} 项数与尺码数不符、倍数
非法）时所有尺码一并停止，错误中给出原文行号与字符位置。每个尺码的
final 为最后一行有效推算后的针数（全部通过时即最终针数）。

线材用量按每个尺码每行各记号的实际出现次数（嵌套与按尺码重复均计入）
乘以单记号克重累计；遇针数错误的尺码停止后续行，其用量为部分估算
（partial=true）；语法错误同样导致全部尺码的用量为部分估算。含损耗
总量 = 原始总量 ×(1+waste_percent/100)，线团数为其除以每团克重后
向上取整。

倍数修正建议：某行可解析但至少一个尚在推算的尺码针数不符时，针对首个
这样的行逐一尝试“仅改该行一个现有的 *n 或 *{...} 倍数”（嵌套重复只按
算术推导，不展开）。建议行须让本行所有尚在推算的尺码消耗恰好等于各自
可用针数，倍数保持正整数，原本正确的尺码不受影响。候选按（改动的尺码
项数, 倍数变化总量, 原文位置）升序排列，首条放入响应顶层的 "suggestion"
（不改变原有结果字段）；无解或没有针数差额时 suggestion 为 null。
"""

import math
import os
import re

from flask import Flask, jsonify, render_template, request

from stitch_parser import (
    STITCH_NAMES,
    ParseError,
    PREVIEW_PAGE_SIZE,
    build_stitch_plan,
    parse_row,
    parse_row_details,
    plan_stitch_page,
    reorder_size_multipliers,
    suggest_row_fix,
)

app = Flask(__name__)


def _parse_cast_on(raw):
    """校验起针数，返回 (数值, 错误消息)。只接受正整数。"""
    if isinstance(raw, bool):  # bool 是 int 的子类，需先排除
        return None, "起针数必须为正整数"
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        s = raw.strip()
        if not s.isdigit():
            return None, "起针数必须为正整数"
        value = int(s)
    else:
        return None, "起针数必须为正整数"
    if value <= 0:
        return None, "起针数必须为正整数（大于 0）"
    return value, None


def _parse_sizes(raw):
    """校验尺码列表，返回 ([{"name", "cast_on"}, ...], 错误消息)。"""
    if not isinstance(raw, list) or not raw:
        return None, "尺码列表不能为空"
    sizes = []
    for idx, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            return None, "第 {} 个尺码格式错误，应为 {{\"name\", \"cast_on\"}}".format(idx)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            return None, "第 {} 个尺码的名称不能为空".format(idx)
        cast_on, err = _parse_cast_on(item.get("cast_on"))
        if err:
            return None, "尺码 {} 的起针数无效：{}".format(name.strip(), err)
        sizes.append({"name": name.strip(), "cast_on": cast_on})
    return sizes, None


# 线材参数字段 -> 中文标签
_YARN_FIELDS = {
    'k': '下针 k',
    'p': '上针 p',
    'yo': '挂线 yo',
    'k2tog': '两针并一针 k2tog',
    'waste_percent': '损耗百分比',
    'grams_per_ball': '每团克重',
}

_NUMBER_RE = re.compile(r'^[+-]?(\d+(?:\.\d*)?|\.\d+)$')


def _parse_nonneg_number(raw, label, positive=False):
    """解析非负（positive=True 时必须严格为正）数值，接受数字或纯数字字符串。

    拒绝布尔值、指数记法（如 1e2）与无法解析的内容，返回 (数值, 错误消息)。
    """
    if isinstance(raw, bool):
        return None, "{}必须为数值".format(label)
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str) and _NUMBER_RE.match(raw.strip()):
        value = float(raw.strip())
    else:
        return None, "{}必须为数值".format(label)
    if not math.isfinite(value) or value < 0:
        return None, "{}不能为负数".format(label)
    if positive and value <= 0:
        return None, "{}必须大于 0".format(label)
    return value, None


def _parse_yarn(raw):
    """校验线材用量参数，返回 (dict 或 None, 错误消息)。

    缺省（不传 yarn）时返回 (None, None)，表示不做用量估算。
    """
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, "线材参数格式错误，应为对象 {\"k\", \"p\", ...}"
    yarn = {}
    for key in ('k', 'p', 'yo', 'k2tog', 'waste_percent'):
        if key not in raw:
            return None, "缺少{}参数".format(_YARN_FIELDS[key])
        value, err = _parse_nonneg_number(raw[key], _YARN_FIELDS[key])
        if err:
            return None, err
        yarn[key] = value
    if 'grams_per_ball' not in raw:
        return None, "缺少{}参数".format(_YARN_FIELDS['grams_per_ball'])
    value, err = _parse_nonneg_number(
        raw['grams_per_ball'], _YARN_FIELDS['grams_per_ball'], positive=True)
    if err:
        return None, err
    yarn['grams_per_ball'] = value
    return yarn, None


def _parse_error_obj(lineno, text, exc):
    """构造解析错误对象，附带原文行号与字符位置（1 起）。"""
    err = {
        "line": lineno,
        "type": "parse",
        "instruction": text,
        "message": str(exc),
    }
    if getattr(exc, "pos", None) is not None:
        err["char"] = exc.pos + 1
    return err


def _count_error_obj(lineno, text, available, consume):
    """构造针数差额错误对象。"""
    diff = consume - available
    if diff > 0:
        message = "需要消耗 {} 针，但仅有 {} 针可用，缺少 {} 针".format(
            consume, available, diff)
    else:
        message = "只消耗了 {} 针，可用的 {} 针还剩 {} 针未使用".format(
            consume, available, -diff)
    return {
        "line": lineno,
        "type": "count",
        "instruction": text,
        "available": available,
        "consume": consume,
        "diff": diff,
        "message": message,
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/validate")
def validate():
    data = request.get_json(silent=True) or {}

    cast_on, err = _parse_cast_on(data.get("cast_on"))
    if err:
        return jsonify({
            "ok": False,
            "rows": [],
            "error": {"line": None, "type": "cast_on", "message": err},
        })

    pattern = data.get("pattern", "")
    if not isinstance(pattern, str):
        pattern = str(pattern)

    rows = []
    error = None
    available = cast_on
    for lineno, raw_line in enumerate(pattern.splitlines(), 1):
        text = raw_line.strip()
        if not text:  # 跳过空行，行号仍按原始文本计算
            continue
        try:
            consume, produce = parse_row(text)
        except ParseError as exc:
            error = _parse_error_obj(lineno, text, exc)
            break
        if consume != available:
            error = _count_error_obj(lineno, text, available, consume)
            break
        rows.append({
            "line": lineno,
            "instruction": text,
            "in": available,
            "consume": consume,
            "produce": produce,
            "out": produce,
        })
        available = produce

    result = {"ok": error is None, "rows": rows, "error": error}
    if error is None:
        result["final"] = available
    return jsonify(result)


@app.post("/api/validate-sizes")
def validate_sizes():
    data = request.get_json(silent=True) or {}

    sizes, err = _parse_sizes(data.get("sizes"))
    if err:
        return jsonify({
            "ok": False,
            "sizes": [],
            "error": {"line": None, "type": "sizes", "message": err},
        })

    yarn, err = _parse_yarn(data.get("yarn"))
    if err:
        return jsonify({
            "ok": False,
            "sizes": [],
            "error": {"line": None, "type": "yarn", "message": err},
        })

    pattern = data.get("pattern", "")
    if not isinstance(pattern, str):
        pattern = str(pattern)

    num_sizes = len(sizes)
    states = [{
        "name": s["name"],
        "cast_on": s["cast_on"],
        "available": s["cast_on"],
        "rows": [],
        "error": None,
        "active": True,
        "raw_weight": 0.0 if yarn is not None else None,
    } for s in sizes]

    first_count_error = None  # (行号, 原文, 各尺码可用针数)：首个针数差额行
    for lineno, raw_line in enumerate(pattern.splitlines(), 1):
        if not any(st["active"] for st in states):
            break  # 所有尺码都已停止，后续行不再推算
        text = raw_line.strip()
        if not text:  # 跳过空行，行号仍按原始文本计算
            continue
        try:
            consumes, produces, tallies = parse_row_details(text, num_sizes)
        except ParseError as exc:
            # 指令非法时任何尺码都无法继续，全部停止并记录同一错误
            error = _parse_error_obj(lineno, text, exc)
            for st in states:
                if st["active"]:
                    st["error"] = error
                    st["active"] = False
            break
        if first_count_error is None and any(
                st["active"] and consumes[i] != st["available"]
                for i, st in enumerate(states)):
            # 记录首个针数差额行的现场，供事后推导倍数修正建议
            first_count_error = (lineno, text, [
                st["available"] if st["active"] else None for st in states])
        for i, st in enumerate(states):
            if not st["active"]:
                continue
            consume, produce = consumes[i], produces[i]
            if consume != st["available"]:
                # 仅停止该尺码，其余尺码继续推算
                st["error"] = _count_error_obj(lineno, text, st["available"], consume)
                st["active"] = False
                continue
            row = {
                "line": lineno,
                "instruction": text,
                "in": st["available"],
                "consume": consume,
                "produce": produce,
                "out": produce,
            }
            if yarn is not None:
                # 按该尺码本行各记号的实际次数累计原始克重（含嵌套/按尺码重复）
                st["raw_weight"] += sum(
                    tallies[name][i] * yarn[name] for name in STITCH_NAMES)
                row["weight"] = round(st["raw_weight"], 2)
            st["rows"].append(row)
            st["available"] = produce

    suggestion = None
    if first_count_error is not None:
        err_line, err_text, availables = first_count_error
        fix = suggest_row_fix(err_text, availables)
        if fix is not None:
            suggestion = {
                "line": err_line,
                "char": fix["char"],
                "old": fix["old"],
                "new": fix["new"],
                "instruction": fix["instruction"],
                "produces": fix["produces"],
            }

    result_sizes = []
    for st in states:
        result = {
            "name": st["name"],
            "cast_on": st["cast_on"],
            "ok": st["error"] is None,
            "rows": st["rows"],
            "error": st["error"],
            "final": st["available"],
        }
        if yarn is not None:
            result["yarn"] = _yarn_summary(yarn, st)
        result_sizes.append(result)
    return jsonify({
        "ok": all(s["ok"] for s in result_sizes),
        "sizes": result_sizes,
        "suggestion": suggestion,
    })


@app.post("/api/move-size")
def move_size():
    """尺码上移/下移，并同步重排指令中所有 *{...} 的倍数项。

    请求: {"sizes": [...], "pattern": "...", "index": 0, "direction": "up"|"down"}
    成功: {"ok": true, "sizes": 重排后的尺码列表, "pattern": 重排后的指令原文}
    失败: {"ok": false, "error": {"type": "sizes"|"move"|"reorder", ...}}
      reorder 错误带 "line"（原文行号）与 "char"（字符位置，均从 1 计）；
      此时尺码列表与指令均不改动，整次移动不生效。
    """
    data = request.get_json(silent=True) or {}

    sizes, err = _parse_sizes(data.get("sizes"))
    if err:
        return jsonify({
            "ok": False,
            "error": {"line": None, "type": "sizes", "message": err},
        })

    index = data.get("index")
    direction = data.get("direction")
    if isinstance(index, bool) or not isinstance(index, int):
        return jsonify({
            "ok": False,
            "error": {"line": None, "type": "move", "message": "缺少要移动的尺码位置"},
        })
    if direction not in ("up", "down"):
        return jsonify({
            "ok": False,
            "error": {"line": None, "type": "move", "message": "移动方向必须是 up 或 down"},
        })

    target = index - 1 if direction == "up" else index + 1
    if not (0 <= index < len(sizes)) or not (0 <= target < len(sizes)):
        return jsonify({
            "ok": False,
            "error": {"line": None, "type": "move",
                      "message": "该尺码已在边界，无法继续{}".format(
                          "上移" if direction == "up" else "下移")},
        })

    pattern = data.get("pattern", "")
    if not isinstance(pattern, str):
        pattern = str(pattern)

    num_sizes = len(sizes)
    order = list(range(num_sizes))
    order[index], order[target] = order[target], order[index]

    line_ends = [(m.start(), m.end()) for m in re.finditer(r'\r\n|\r|\n', pattern)]
    line_starts = [0] + [end for _start, end in line_ends]

    new_lines = []
    for lineno, start in enumerate(line_starts, 1):
        end = line_ends[lineno - 1][0] if lineno - 1 < len(line_ends) else len(pattern)
        line_text = pattern[start:end]
        try:
            new_lines.append(reorder_size_multipliers(line_text, order))
        except ParseError as exc:
            error = {
                "line": lineno,
                "type": "reorder",
                "message": str(exc),
            }
            if getattr(exc, "pos", None) is not None:
                error["char"] = exc.pos + 1
            return jsonify({"ok": False, "error": error})

    # 逐行重排后用原换行符拼回，保留原文其余字节不变
    rebuilt = []
    for lineno, new_text in enumerate(new_lines):
        rebuilt.append(new_text)
        if lineno < len(line_ends):
            ls, le = line_ends[lineno]
            rebuilt.append(pattern[ls:le])
    new_pattern = "".join(rebuilt)

    new_sizes = [None] * num_sizes
    for new_idx, old_idx in enumerate(order):
        new_sizes[new_idx] = sizes[old_idx]
    return jsonify({"ok": True, "sizes": new_sizes, "pattern": new_pattern})


def _preview_error_obj(err_type, message, line=None, char=None):
    """构造针位预览的错误对象；parse 错误可附带原文行号与字符位置。"""
    err = {"type": err_type, "message": message}
    if line is not None:
        err["line"] = line
    if char is not None:
        err["char"] = char
    return err


@app.post("/api/preview-row")
def preview_row():
    """单行针位预览：按图解原文行、尺码与起始操作序号返回一页实际针法。

    请求: {"sizes": [...], "pattern": "...", "line": 1, "size": 0, "start": 1}
    line 为原文行号（1 起），size 为尺码下标（0 起），start 为起始操作序号
    （1 起），每页最多 PREVIEW_PAGE_SIZE 个针法。嵌套重复与按尺码倍数按所选
    尺码算术定位、跳过未显示的重复段，不展开整行；非法行返回现有解析错误与
    原文位置，不给部分预览。
    """
    data = request.get_json(silent=True) or {}

    sizes, err = _parse_sizes(data.get("sizes"))
    if err:
        return jsonify({
            "ok": False,
            "error": _preview_error_obj("sizes", err),
        })
    num_sizes = len(sizes)

    pattern = data.get("pattern", "")
    if not isinstance(pattern, str):
        pattern = str(pattern)

    line = data.get("line")
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        return jsonify({
            "ok": False,
            "error": _preview_error_obj("line", "行号必须为从 1 开始的正整数"),
        })

    raw_lines = pattern.splitlines()
    if line > len(raw_lines):
        return jsonify({
            "ok": False,
            "error": _preview_error_obj(
                "line", "行号超出原文范围（共 {} 行）".format(len(raw_lines))),
        })
    text = raw_lines[line - 1].strip()
    if not text:  # 空行不是指令，无法预览
        return jsonify({
            "ok": False,
            "error": _preview_error_obj("line", "第 {} 行为空，没有可预览的指令".format(line)),
        })

    size_index = data.get("size", 0)
    if isinstance(size_index, bool) or not isinstance(size_index, int) \
            or not (0 <= size_index < num_sizes):
        return jsonify({
            "ok": False,
            "error": _preview_error_obj(
                "size", "尺码下标必须在 0..{} 之间".format(num_sizes - 1)),
        })

    # 先按当前输入与解析语法构造计划：非法行在此抛出现有 ParseError（带位置）
    try:
        plan = build_stitch_plan(text, num_sizes, size_index)
    except ParseError as exc:
        return jsonify({
            "ok": False,
            "error": _parse_error_obj(line, text, exc),
        })

    start = data.get("start", 1)
    if isinstance(start, bool) or not isinstance(start, int) or start < 1:
        return jsonify({
            "ok": False,
            "error": _preview_error_obj("start", "起始操作序号必须为正整数"),
        })
    if start > plan.total_stitches:
        return jsonify({
            "ok": False,
            "error": _preview_error_obj(
                "start", "起始序号超出该行实际针法总数（共 {} 个）".format(
                    plan.total_stitches)),
        })

    stitches = plan_stitch_page(plan, start, PREVIEW_PAGE_SIZE)
    end = start + len(stitches) - 1
    return jsonify({
        "ok": True,
        "line": line,
        "instruction": text,
        "size": size_index,
        "size_name": sizes[size_index]["name"],
        "total_stitches": plan.total_stitches,
        "total_consume": plan.total_consume,
        "total_produce": plan.total_produce,
        "page_size": PREVIEW_PAGE_SIZE,
        "start": start,
        "end": end,
        "has_prev": start > 1,
        "has_next": end < plan.total_stitches,
        "prev_start": max(1, start - PREVIEW_PAGE_SIZE),
        "next_start": end + 1 if end < plan.total_stitches else None,
        "stitches": stitches,
    })


def _yarn_summary(yarn, state):
    """按尺码累计结果构造线材用量汇总。"""
    raw_total = round(state["raw_weight"], 2)
    factor = 1 + yarn["waste_percent"] / 100.0
    with_waste_exact = state["raw_weight"] * factor
    with_waste = round(with_waste_exact, 2)
    # 抵消浮点误差，避免 3.0000000000000004 被向上取整成 4
    balls = math.ceil(with_waste_exact / yarn["grams_per_ball"] - 1e-9)
    partial = state["error"] is not None
    return {
        "raw_total": raw_total,
        "with_waste": with_waste,
        "balls": max(balls, 0),
        "partial": partial,
        "complete": not partial,
        "waste_percent": yarn["waste_percent"],
        "grams_per_ball": yarn["grams_per_ball"],
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
