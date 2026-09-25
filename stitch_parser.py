"""编织指令解析器：按括号与倍数计算每行的消耗/产出针数。

支持的记号（大小写不敏感，可用空格或逗号分隔）：
  k       下针：消耗 1 针，产出 1 针
  p       上针：消耗 1 针，产出 1 针
  yo      挂线：不消耗，产出 1 针
  k2tog   两针并一针：消耗 2 针，产出 1 针
  (序列)*n          括号序列重复 n 次，可嵌套；单个记号后也可跟 *n
  (序列)*{a|b|c}    按尺码分别取倍数：竖线分隔的正整数按尺码顺序一一对应，
                    项数必须与尺码个数一致；单个记号后同样可跟 *{a|b|c}

普通 *n 对所有尺码生效，*{a|b|c} 与 *n 两种重复可互相嵌套。
解析只做算术累计，绝不展开重复项，因此 (k)*1000000000 也能瞬间算完。

除消耗/产出针数外，解析器还可返回每种记号（k/p/yo/k2tog）在每行中每个
尺码的实际出现次数（parse_row_details / parse_row_stitch_counts），供线材
用量按“记号次数 × 单记号克重”累计。

suggest_row_fix 为“可解析但至少一个尺码针数不符”的行推导修正建议：整行
消耗关于任何一个倍数都是线性的，用 *1、*2 两次试算即可推出“基数 + 系数
× 倍数”（嵌套重复同样只做算术推导，不展开），进而解出让各尺码消耗恰好
等于可用针数的正整数倍数。

reorder_size_multipliers 在尺码顺序调整（上移/下移）时，按新顺序重排一行
中所有 *{a|b|...} 的倍数项：嵌套重复与同一行多处都会处理，普通 *n 与其他
原文保持不变；任一处项数或数值不合法时抛 ParseError 并给出字符位置，调用
方可据此中止整次移动。

单行针位预览（build_stitch_plan / plan_stitch_page）：把一行指令解析成带
倍数信息的语法树（不展开重复），再按所选尺码为每个节点算出该尺码下的消耗/
产出与重复次数。翻页时用算术定位直接跳过起始序号之前的整段重复（含嵌套与
按尺码倍数），只逐个生成当前页的实际针法：每个针法给出消耗的入针范围
（行内 1 起）与产出的出针位置，挂线（yo）零入针，额外给出 insert_after
标明插入在已消耗的第几针之后。因此 (k)*1000000000 也能直接查看尾段。
"""

import re

# 记号顺序（稳定的遍历顺序）
STITCH_NAMES = ('k', 'p', 'yo', 'k2tog')

# 记号 -> (消耗针数, 产出针数)
STITCH_COUNTS = {
    'k': (1, 1),
    'p': (1, 1),
    'yo': (0, 1),
    'k2tog': (2, 1),
}

# 注意 k2tog 必须排在 k 之前，保证最长匹配
TOKEN_RE = re.compile(r"""
    (?P<STITCH>k2tog|yo|k|p)
  | (?P<LPAREN>\()
  | (?P<RPAREN>\))
  | (?P<STAR>\*)
  | (?P<LBRACE>\{)
  | (?P<RBRACE>\})
  | (?P<PIPE>\|)
  | (?P<NUM>\d+)
  | (?P<SEP>[\s,]+)
""", re.IGNORECASE | re.VERBOSE)


class ParseError(ValueError):
    """指令解析错误，message 中已包含定位信息。

    pos 为出错字符在原文中的下标（从 0 计），供接口返回结构化的字符位置；
    无法定位到具体字符时为 None。
    """

    def __init__(self, message, pos=None):
        super().__init__(message)
        self.pos = pos


def _tokenize(text):
    tokens = []
    pos = 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise ParseError(
                "无法识别的记号 {!r}（第 {} 个字符）".format(text[pos], pos + 1), pos)
        if m.lastgroup != 'SEP':
            tokens.append((m.lastgroup, m.group(), m.start()))
        pos = m.end()
    return tokens


class _Parser(object):
    def __init__(self, tokens, num_sizes):
        self.tokens = tokens
        self.i = 0
        self.num_sizes = num_sizes

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def advance(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse_row(self):
        """row := sequence EOF

        返回 (consumes, produces, tallies)：consumes/produces 为长度 num_sizes
        的针数列表；tallies 为 {记号名: 长度 num_sizes 的次数列表}。
        """
        c, p, tallies = self.parse_sequence()
        tok = self.peek()
        if tok is not None:
            kind, val, pos = tok
            if kind == 'RPAREN':
                raise ParseError("多余的右括号 ')'（第 {} 个字符）".format(pos + 1), pos)
            if kind == 'NUM':
                raise ParseError(
                    "意外的数字 {!r}（第 {} 个字符）：倍数需写成 (序列)*次数 的形式".format(val, pos + 1), pos)
            if kind == 'STAR':
                raise ParseError(
                    "意外的 '*'（第 {} 个字符）：'*' 必须跟在记号或 ')' 之后".format(pos + 1), pos)
            raise ParseError("意外的记号 {!r}（第 {} 个字符）".format(val, pos + 1), pos)
        return c, p, tallies

    def parse_sequence(self):
        """sequence := item+，消耗/产出/记号次数逐项累加"""
        total_c = [0] * self.num_sizes
        total_p = [0] * self.num_sizes
        total_t = {name: [0] * self.num_sizes for name in STITCH_NAMES}
        while True:
            tok = self.peek()
            if tok is None or tok[0] not in ('STITCH', 'LPAREN'):
                break
            c, p, tallies = self.parse_item()
            for j in range(self.num_sizes):
                total_c[j] += c[j]
                total_p[j] += p[j]
                for name in STITCH_NAMES:
                    total_t[name][j] += tallies[name][j]
        return total_c, total_p, total_t

    def parse_item(self):
        """item := (STITCH | '(' sequence ')') ('*' repeat)?"""
        kind, val, pos = self.advance()
        if kind == 'STITCH':
            c0, p0 = STITCH_COUNTS[val.lower()]
            c = [c0] * self.num_sizes
            p = [p0] * self.num_sizes
            tallies = {name: [0] * self.num_sizes for name in STITCH_NAMES}
            tallies[val.lower()] = [1] * self.num_sizes
        else:  # LPAREN
            c, p, tallies = self.parse_sequence()
            if not any(c) and not any(p):
                raise ParseError("括号内不能为空（第 {} 个字符）".format(pos + 1), pos)
            tok = self.peek()
            if tok is None:
                raise ParseError("缺少与 '(' 对应的 ')'（第 {} 个字符）".format(pos + 1), pos)
            if tok[0] != 'RPAREN':
                raise ParseError(
                    "括号内出现意外的记号 {!r}（第 {} 个字符）".format(tok[1], tok[2] + 1), tok[2])
            self.advance()
        tok = self.peek()
        if tok is not None and tok[0] == 'STAR':
            self.advance()
            mults = self.parse_repeat(tok)
            c = [cv * m for cv, m in zip(c, mults)]
            p = [pv * m for pv, m in zip(p, mults)]
            tallies = {
                name: [cnt * m for cnt, m in zip(counts, mults)]
                for name, counts in tallies.items()
            }
        return c, p, tallies

    def parse_repeat(self, star_tok):
        """repeat := NUM | '{' NUM ('|' NUM)* '}'，返回长度 num_sizes 的倍数列表。

        普通 *n 对所有尺码生效；*{a|b|c} 按尺码顺序分别取值，项数必须等于尺码数。
        """
        star_pos = star_tok[2]
        tok = self.peek()
        if tok is None:
            raise ParseError(
                "'*' 后必须跟正整数倍数（第 {} 个字符）".format(star_pos + 1), star_pos)
        if tok[0] == 'NUM':
            self.advance()
            return [self._positive_num(tok)] * self.num_sizes
        if tok[0] == 'LBRACE':
            self.advance()
            values = []
            while True:
                ntok = self.peek()
                if ntok is None:
                    raise ParseError(
                        "尺码倍数 '{{...}}' 未闭合，缺少正整数（第 {} 个字符）".format(tok[2] + 1), tok[2])
                if ntok[0] != 'NUM':
                    raise ParseError(
                        "尺码倍数 '{{...}}' 中必须是正整数，收到 {!r}（第 {} 个字符）".format(
                            ntok[1], ntok[2] + 1), ntok[2])
                self.advance()
                values.append(self._positive_num(ntok))
                sep = self.peek()
                if sep is not None and sep[0] == 'PIPE':
                    self.advance()
                    continue
                if sep is not None and sep[0] == 'RBRACE':
                    self.advance()
                    break
                if sep is None:
                    raise ParseError(
                        "尺码倍数 '{{...}}' 缺少右括号 '}}'（第 {} 个字符）".format(tok[2] + 1), tok[2])
                raise ParseError(
                    "尺码倍数 '{{...}}' 的数字需用 '|' 分隔，收到 {!r}（第 {} 个字符）".format(
                        sep[1], sep[2] + 1), sep[2])
            if len(values) != self.num_sizes:
                raise ParseError(
                    "尺码倍数项数不符：共 {} 个尺码，但倍数给出了 {} 个值（第 {} 个字符）".format(
                        self.num_sizes, len(values), star_pos + 1), star_pos)
            return values
        raise ParseError(
            "'*' 后必须跟正整数倍数或按尺码的 '{{a|b|...}}'（第 {} 个字符）".format(tok[2] + 1), tok[2])

    @staticmethod
    def _positive_num(ntok):
        n = int(ntok[1])
        if n <= 0:
            raise ParseError(
                "倍数必须为正整数，收到 {}（第 {} 个字符）".format(n, ntok[2] + 1), ntok[2])
        return n


def parse_row_details(text, num_sizes):
    """解析一行指令，返回 (consumes, produces, tallies)。

    consumes/produces 为各尺码的消耗/产出针数列表（长度 num_sizes）；
    tallies 为 {记号名: 各尺码该记号次数列表}，嵌套重复与按尺码重复均按
    实际次数算术累计（不展开重复项）。非法输入抛出 ParseError。
    """
    if num_sizes < 1:
        raise ValueError("num_sizes 必须为正整数")
    tokens = _tokenize(text)
    if not tokens:
        raise ParseError("该行没有有效指令")
    return _Parser(tokens, num_sizes).parse_row()


def parse_row_sizes(text, num_sizes):
    """解析一行指令，返回 (consumes, produces)：各尺码的消耗/产出针数列表。

    两个列表长度均等于 num_sizes，按尺码顺序排列；非法输入抛出 ParseError。
    """
    consumes, produces, _tallies = parse_row_details(text, num_sizes)
    return consumes, produces


def parse_row_stitch_counts(text, num_sizes):
    """解析一行指令，返回各尺码的记号次数 {记号名: 长度 num_sizes 的次数列表}。"""
    _c, _p, tallies = parse_row_details(text, num_sizes)
    return tallies


def parse_row(text):
    """单尺码解析：返回 (消耗针数, 产出针数)。非法输入抛出 ParseError。"""
    consumes, produces, _tallies = parse_row_details(text, 1)
    return consumes[0], produces[0]


def _repeat_sites(text, num_sizes):
    """列出行中每个倍数的位置与各尺码当前值（仅供已确认可解析的行使用）。

    返回 [(star_pos, end, values), ...]：star_pos 为 '*' 在原文中的下标，
    end 为整个倍数文本（从 '*' 起）的结束下标（开区间）；values 为长度
    num_sizes 的各尺码当前倍数（普通 *n 展开为 num_sizes 个相同值）。
    """
    sites = []
    tokens = _tokenize(text)
    i = 0
    while i < len(tokens):
        kind, _val, pos = tokens[i]
        if kind != 'STAR':
            i += 1
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is not None and nxt[0] == 'NUM':
            sites.append((pos, nxt[2] + len(nxt[1]), [int(nxt[1])] * num_sizes))
            i += 2
            continue
        if nxt is not None and nxt[0] == 'LBRACE':
            values = []
            j = i + 1
            while j < len(tokens) and tokens[j][0] != 'RBRACE':
                if tokens[j][0] == 'NUM':
                    values.append(int(tokens[j][1]))
                j += 1
            end = tokens[j][2] + 1 if j < len(tokens) else len(text)
            sites.append((pos, end, values))
            i = j + 1
            continue
        i += 1  # 孤立的 '*' 只出现在不可解析的行中，调用方已排除
    return sites


def _try_multiplier_fix(text, site, availables, num_sizes):
    """尝试仅改 site 处的一个倍数让各尺码消耗等于可用针数。

    成功返回 ((改动的尺码项数, 倍数变化总量, 原文位置), 候选 dict)，否则
    返回 None。整行消耗关于该倍数是线性的：用 *1、*2 各试算一次即可推出
    “基数 + 系数 × 倍数”，嵌套重复只参与算术推导，不展开。
    """
    star_pos, end, current = site
    try:
        c1 = parse_row_sizes(text[:star_pos] + '*1' + text[end:], num_sizes)[0]
        c2 = parse_row_sizes(text[:star_pos] + '*2' + text[end:], num_sizes)[0]
    except ParseError:
        return None
    new_values = []
    changed = 0
    delta = 0
    for i in range(num_sizes):
        coeff = c2[i] - c1[i]  # 该倍数每 +1，此尺码整行消耗的增量
        base = c1[i] - coeff   # 行内其余部分的消耗
        target = availables[i]
        if target is None:
            new = current[i]   # 已停止推算的尺码：保持原倍数
        elif coeff == 0:
            if base != target:
                return None    # 此倍数不影响该尺码，差额无法借此修正
            new = current[i]
        else:
            rest = target - base
            if rest <= 0 or rest % coeff != 0:
                return None    # 倍数必须为正整数
            new = rest // coeff
        new_values.append(new)
        if new != current[i]:
            changed += 1
            delta += abs(new - current[i])
    if changed == 0:
        return None            # 没有实际改动
    if all(v == new_values[0] for v in new_values):
        replacement = '*{}'.format(new_values[0])
    else:
        replacement = '*{' + '|'.join(str(v) for v in new_values) + '}'
    new_text = text[:star_pos] + replacement + text[end:]
    try:
        consumes, produces = parse_row_sizes(new_text, num_sizes)
    except ParseError:
        return None
    for i in range(num_sizes):
        if availables[i] is not None and consumes[i] != availables[i]:
            return None
    candidate = {
        'char': star_pos + 1,  # '*' 的字符位置（从 1 计）
        'old': text[star_pos:end],
        'new': replacement,
        'instruction': new_text,
        'produces': produces,
    }
    return (changed, delta, star_pos), candidate


def suggest_row_fix(text, availables):
    """为可解析但至少一个尺码针数不符的行寻找“仅改一个倍数”的修正建议。

    availables 为各尺码当前可用针数；None 表示该尺码已停止推算，不参与
    约束且倍数保持原值。候选必须让所有参与约束的尺码消耗恰好等于可用
    针数（原本正确的尺码不受影响），倍数保持正整数。多个候选按
    （改动的尺码项数, 倍数变化总量, 原文位置）升序排列，返回首条：
      {"char", "old", "new", "instruction", "produces"}
    各尺码新倍数相同时建议写作普通 *n，否则写作 *{a|b|c}。
    无解（或行本身不可解析）时返回 None。
    """
    num_sizes = len(availables)
    if num_sizes < 1:
        raise ValueError("availables 不能为空")
    try:
        parse_row_details(text, num_sizes)
    except ParseError:
        return None
    best = None
    for site in _repeat_sites(text, num_sizes):
        result = _try_multiplier_fix(text, site, availables, num_sizes)
        if result is not None and (best is None or result[0] < best[0]):
            best = result
    return best[1] if best is not None else None


def reorder_size_multipliers(text, order):
    """按新尺码顺序重排一行中每个 *{a|b|...} 的倍数项，返回重排后的行。

    order 为长度等于尺码数的下标排列（0 起）：新顺序中第 i 项取自原顺序
    第 order[i] 项。普通 *n 与其余原文（含空白、分隔符）原样保留；
    *{...} 中的数值文本（含前导零等）按原文本整体搬移，嵌套重复与同一行
    多处倍数都会处理。任一处 *{...} 项数与尺码数不符或数值非法（0、缺失、
    无法识别的字符、括号未闭合等）时抛 ParseError 并给出字符位置，使整次
    移动可以整体中止。
    """
    n = len(order)
    if sorted(order) != list(range(n)):
        raise ValueError("order 必须是 0..尺码数-1 的一个排列")

    edits = []  # (数字在原文中的 (起始, 结束), 搬移来的数字文本)
    pos = 0
    while pos < len(text):
        if text[pos] != '*':
            pos += 1
            continue
        j = pos + 1
        # 与解析器一致：'*' 与倍数之间允许空白或逗号
        while j < len(text) and (text[j].isspace() or text[j] == ','):
            j += 1
        if j >= len(text) or text[j] != '{':
            pos = j if j > pos + 1 else pos + 1
            continue
        brace_pos = j
        k = j + 1
        spans = []
        while True:
            while k < len(text) and text[k].isspace():
                k += 1
            start = k
            while k < len(text) and text[k].isdigit():
                k += 1
            if k == start:
                if k >= len(text):
                    raise ParseError(
                        "尺码倍数 '{{...}}' 未闭合，缺少正整数（第 {} 个字符）".format(
                            brace_pos + 1), brace_pos)
                raise ParseError(
                    "尺码倍数 '{{...}}' 中必须是正整数，收到 {!r}（第 {} 个字符）".format(
                        text[k], k + 1), k)
            value = int(text[start:k])
            if value <= 0:
                raise ParseError(
                    "倍数必须为正整数，收到 {}（第 {} 个字符）".format(value, start + 1), start)
            spans.append((start, k))
            while k < len(text) and text[k].isspace():
                k += 1
            if k < len(text) and text[k] == '|':
                k += 1
                continue
            if k < len(text) and text[k] == '}':
                k += 1
                break
            if k >= len(text):
                raise ParseError(
                    "尺码倍数 '{{...}}' 缺少右括号 '}}'（第 {} 个字符）".format(
                        brace_pos + 1), brace_pos)
            raise ParseError(
                "尺码倍数 '{{...}}' 的数字需用 '|' 分隔，收到 {!r}（第 {} 个字符）".format(
                    text[k], k + 1), k)
        if len(spans) != n:
            raise ParseError(
                "尺码倍数项数不符：共 {} 个尺码，但倍数给出了 {} 个值（第 {} 个字符）".format(
                    n, len(spans), pos + 1), pos)
        for new_idx, (s, e) in enumerate(spans):
            src_s, src_e = spans[order[new_idx]]
            edits.append(((s, e), text[src_s:src_e]))
        pos = k

    # 各段互不重叠且按位置升序，倒序替换即可保持下标有效
    result = text
    for (s, e), replacement in reversed(edits):
        result = result[:s] + replacement + result[e:]
    return result


# ---------------------------------------------------------------------------
# 单行针位预览：带倍数的语法树 + 算术定位（不展开重复）
# ---------------------------------------------------------------------------

class _Ast(object):
    """语法树节点：叶子为单个记号，组节点为括号序列。

    values 为 None（普通 *n，对所有尺码同倍数）或长度 num_sizes 的倍数列表；
    rep/c/p 由 _annotate 按所选尺码填入：rep 为该节点在该尺码下的重复次数，
    c/p 为该节点一次出现（不乘 rep）在该尺码下的消耗/产出针数。
    """

    __slots__ = ('kind', 'name', 'children', 'values', 'rep', 'c', 'p')

    def __init__(self, kind, name=None, children=None, values=None):
        self.kind = kind          # 'leaf' 或 'group'
        self.name = name          # 叶子：记号名（小写）
        self.children = children  # 组节点：子节点列表
        self.values = values      # 倍数：None=普通 *1/无倍数，list=按尺码
        self.rep = 1
        self.c = 0
        self.p = 0


class _AstParser(object):
    """由词法 token 构造 _Ast 树。仅供已通过 parse_row_details 的行使用：

    语法已确认合法，这里不重复报错，只按同一语法重新建树。
    """

    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def advance(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        children = self.parse_sequence()
        return _Ast('group', children=children, values=None)

    def parse_sequence(self):
        nodes = []
        while True:
            tok = self.peek()
            if tok is None or tok[0] not in ('STITCH', 'LPAREN'):
                break
            nodes.append(self.parse_item())
        return nodes

    def parse_item(self):
        kind, val, _pos = self.advance()
        if kind == 'STITCH':
            node = _Ast('leaf', name=val.lower(), values=None)
        else:  # LPAREN，合法性已由解析器确认：必有 ')'
            node = _Ast('group', children=self.parse_sequence(), values=None)
            self.advance()  # RPAREN
        tok = self.peek()
        if tok is not None and tok[0] == 'STAR':
            self.advance()
            node.values = self.parse_repeat_values()
        return node

    def parse_repeat_values(self):
        """ '*' 之后的倍数：返回 [int, ...]（普通 *n 为单元素列表）。"""
        tok = self.advance()
        if tok[0] == 'NUM':
            return [int(tok[1])]
        # LBRACE：收集到 RBRACE 为止的数字（合法性已确认，项数等于尺码数）
        values = []
        while True:
            tok = self.advance()
            if tok[0] == 'NUM':
                values.append(int(tok[1]))
            elif tok[0] == 'RBRACE':
                break
        return values


def _annotate(node, size_index):
    """按所选尺码为节点填入 rep/c/p（一次出现的消耗/产出，不乘 rep）。"""
    if node.kind == 'leaf':
        c0, p0 = STITCH_COUNTS[node.name]
        node.c, node.p = c0, p0
    else:
        c = p = 0
        for child in node.children:
            _annotate(child, size_index)
            c += child.c * child.rep
            p += child.p * child.rep
        node.c, node.p = c, p
    if node.values is None:
        node.rep = 1
    elif len(node.values) == 1:
        node.rep = node.values[0]  # 普通 *n 对所有尺码同倍数
    else:
        node.rep = node.values[size_index]  # 按尺码取倍数


class StitchPlan(object):
    """一行在所选尺码下的针位预览计划（算术累计，未展开任何重复）。

    total_stitches 为该尺码下整行实际针法总数；total_consume/total_produce
    为整行入针/出针总数（与 parse_row_details 结果一致）。
    """

    __slots__ = ('root', 'total_consume', 'total_produce', 'total_stitches')

    def __init__(self, root):
        self.root = root
        self.total_consume = root.c * root.rep
        self.total_produce = root.p * root.rep
        total = 0
        for child in root.children:
            total += _subtree_leaf_count(child)
        self.total_stitches = total


def _subtree_leaf_count(node):
    """节点（含自身重复）覆盖的实际针法数。"""
    if node.kind == 'leaf':
        return node.rep
    total = 0
    for child in node.children:
        total += _subtree_leaf_count(child)
    return total * node.rep


def build_stitch_plan(text, num_sizes, size_index):
    """把一行指令解析成所选尺码下的 StitchPlan（非法行抛 ParseError）。

    先以当前输入与解析语法完整校验：行无法解析（含倍数项数与尺码数不符、
    倍数非法等）时抛出现有 ParseError（含字符位置），不产生部分预览。
    嵌套重复与按尺码倍数随后按所选尺码定位（_annotate），全程不展开重复。
    """
    if num_sizes < 1:
        raise ValueError("num_sizes 必须为正整数")
    if not (0 <= size_index < num_sizes):
        raise ValueError("size_index 超出尺码范围")
    # 先做完整语法/算术校验，非法行在此处抛出带位置的 ParseError
    parse_row_details(text, num_sizes)
    tokens = _tokenize(text)
    root = _AstParser(tokens).parse()
    _annotate(root, size_index)
    return StitchPlan(root)


class _Frame(object):
    """延迟展开的遍历栈帧。

    组帧：(node, occ, idx) 表示停在第 occ 次出现（0 起）的第 idx 个子节点前；
    叶帧：(node, occ) 表示下一个可取的是该叶子的第 occ 次出现（0 起）。
    """

    __slots__ = ('node', 'occ', 'idx')

    def __init__(self, node, occ=0, idx=0):
        self.node = node
        self.occ = occ
        self.idx = idx


def _subtree_consume(node):
    """节点整段（含自身重复）的消耗针数。"""
    return node.c * node.rep


def _subtree_produce(node):
    """节点整段（含自身重复）的产出针数。"""
    return node.p * node.rep


def _advance_parent(stack):
    """子帧刚耗尽弹出：父组帧前进到下一个子节点（根帧弹出时栈空）。"""
    if stack and stack[-1].node.kind == 'group':
        stack[-1].idx += 1


def seek_frames(stack, skip):
    """把遍历状态算术推进 skip 个实际针法，返回跳过部分的 (消耗, 产出)。

    整段子节点按 child.c*child.rep 一次算完，整段重复按每次出现的消耗/产出
    乘以次数算完，绝不逐次迭代重复，因此十亿次重复只做常数级运算；不足
    一整段时才压入子帧（嵌套深度有限）。推进后的栈帧状态可直接供 next_leaf 使用。
    """
    done_c = done_p = 0
    while skip > 0 and stack:
        frame = stack[-1]
        node = frame.node
        if node.kind == 'leaf':
            take = min(skip, node.rep - frame.occ)
            frame.occ += take
            done_c += take * node.c
            done_p += take * node.p
            skip -= take
            if frame.occ >= node.rep:
                stack.pop()
                _advance_parent(stack)
            continue
        # 组帧：先整段跳过当前出现中 idx 之后的子节点
        inner_broke = False
        while frame.idx < len(node.children):
            child = node.children[frame.idx]
            count = _subtree_leaf_count(child)
            if skip >= count:
                done_c += _subtree_consume(child)
                done_p += _subtree_produce(child)
                skip -= count
                frame.idx += 1
            else:
                stack.append(_Frame(child))  # 不足一整段：进入子帧由外层继续
                inner_broke = True
                break
        if inner_broke:
            continue
        # 当前出现已耗尽：先计入这一出现，再整段跳过后续完整重复
        per_n = sum(_subtree_leaf_count(c) for c in node.children)
        per_c = sum(_subtree_consume(c) for c in node.children)
        per_p = sum(_subtree_produce(c) for c in node.children)
        frame.occ += 1
        frame.idx = 0
        remaining = node.rep - frame.occ
        full = min(skip // per_n, remaining)
        done_c += full * per_c
        done_p += full * per_p
        skip -= full * per_n
        frame.occ += full
        if frame.occ >= node.rep:
            stack.pop()
            _advance_parent(stack)
        # skip==0 且帧仍在：停在下一出现的开头，该出现尚未消耗
    return done_c, done_p


def next_leaf(stack):
    """取下一个实际针法叶子（返回 _Ast）；遍历结束返回 None。

    只沿栈帧定位：叶子逐次返回，组节点负责进入子节点与推进重复出现，
    不预先展开任何重复段。
    """
    while stack:
        frame = stack[-1]
        node = frame.node
        if node.kind == 'leaf':
            if frame.occ < node.rep:
                frame.occ += 1
                return node
            stack.pop()
            _advance_parent(stack)
            continue
        if frame.idx >= len(node.children):
            # 当前出现的子节点已取完：进入下一出现
            frame.occ += 1
            frame.idx = 0
            if frame.occ >= node.rep:
                stack.pop()
                _advance_parent(stack)
            continue
        stack.append(_Frame(node.children[frame.idx]))
    return None


PREVIEW_PAGE_SIZE = 50


def plan_stitch_page(plan, start, limit=PREVIEW_PAGE_SIZE):
    """返回从第 start 个（1 起）实际针法开始的一页针位条目，最多 limit 个。

    start 必须为 1..total_stitches 的正整数（由调用方校验）。每个条目：
      {"seq", "stitch", "in_start", "in_end", "out_start", "out_end",
       "insert_after"}
    in_start/in_end 为该针法消耗的行内入针范围（1 起）；零入针（yo）时为
    None，并以 insert_after 标明挂线插入在已消耗的第几针之后（0 表示第 1
    针之前）。出针范围对现有记号恰好 1 针（out_start == out_end）。
    """
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        raise ValueError("start 必须为正整数")
    if start > plan.total_stitches:
        raise ValueError("start 超出该行实际针法总数")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit 必须为正整数")

    stack = [_Frame(plan.root)]
    consumed, produced = seek_frames(stack, start - 1)

    entries = []
    while len(entries) < limit:
        leaf = next_leaf(stack)
        if leaf is None:
            break
        seq = start + len(entries)
        in_start = consumed + 1 if leaf.c > 0 else None
        in_end = consumed + leaf.c if leaf.c > 0 else None
        entries.append({
            "seq": seq,
            "stitch": leaf.name,
            "in_start": in_start,
            "in_end": in_end,
            "out_start": produced + 1,
            "out_end": produced + leaf.p,
            "insert_after": consumed if leaf.name == 'yo' else None,
        })
        consumed += leaf.c
        produced += leaf.p
    return entries
