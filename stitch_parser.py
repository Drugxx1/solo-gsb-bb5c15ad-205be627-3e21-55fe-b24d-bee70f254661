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


class PreviewStartError(ValueError):
    """针位预览的起始操作序号超出本行实际针法总数。

    total 为该行（所选尺码下）实际针法总数，调用方可据此提示范围。
    """

    def __init__(self, total):
        super().__init__("起始操作序号超出范围：本行共 {} 个实际针法".format(total))
        self.total = total


# 单行针位预览每页最多显示的实际针法数
PREVIEW_PAGE_SIZE = 50


class _StitchNode(object):
    """解析树中的单个记号节点：mults 为各尺码的重复倍数（长度 num_sizes）。"""

    __slots__ = ('name', 'mults')

    def __init__(self, name, mults):
        self.name = name
        self.mults = mults


class _GroupNode(object):
    """括号序列节点：children 为括号内的节点，mults 为各尺码的重复倍数。"""

    __slots__ = ('children', 'mults')

    def __init__(self, children, mults):
        self.children = children
        self.mults = mults


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

    def parse_row_with_nodes(self):
        """row := sequence EOF

        返回 (consumes, produces, tallies, nodes)：consumes/produces 为长度
        num_sizes 的针数列表；tallies 为 {记号名: 长度 num_sizes 的次数列表}；
        nodes 为顶层节点列表（_StitchNode/_GroupNode），供针位预览定位，
        与算术累计来自同一条解析路径，故两者的语法检查完全一致。
        """
        c, p, tallies, nodes = self.parse_sequence()
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
        return c, p, tallies, nodes

    def parse_sequence(self):
        """sequence := item+，消耗/产出/记号次数逐项累加，同时收集节点"""
        total_c = [0] * self.num_sizes
        total_p = [0] * self.num_sizes
        total_t = {name: [0] * self.num_sizes for name in STITCH_NAMES}
        nodes = []
        while True:
            tok = self.peek()
            if tok is None or tok[0] not in ('STITCH', 'LPAREN'):
                break
            c, p, tallies, node = self.parse_item()
            for j in range(self.num_sizes):
                total_c[j] += c[j]
                total_p[j] += p[j]
                for name in STITCH_NAMES:
                    total_t[name][j] += tallies[name][j]
            nodes.append(node)
        return total_c, total_p, total_t, nodes

    def parse_item(self):
        """item := (STITCH | '(' sequence ')') ('*' repeat)?，同时返回对应节点"""
        kind, val, pos = self.advance()
        if kind == 'STITCH':
            name = val.lower()
            c0, p0 = STITCH_COUNTS[name]
            c = [c0] * self.num_sizes
            p = [p0] * self.num_sizes
            tallies = {name_: [0] * self.num_sizes for name_ in STITCH_NAMES}
            tallies[name] = [1] * self.num_sizes
            node = _StitchNode(name, [1] * self.num_sizes)
        else:  # LPAREN
            c, p, tallies, children = self.parse_sequence()
            if not any(c) and not any(p):
                raise ParseError("括号内不能为空（第 {} 个字符）".format(pos + 1), pos)
            tok = self.peek()
            if tok is None:
                raise ParseError("缺少与 '(' 对应的 ')'（第 {} 个字符）".format(pos + 1), pos)
            if tok[0] != 'RPAREN':
                raise ParseError(
                    "括号内出现意外的记号 {!r}（第 {} 个字符）".format(tok[1], tok[2] + 1), tok[2])
            self.advance()
            node = _GroupNode(children, [1] * self.num_sizes)
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
            node.mults = mults
        return c, p, tallies, node

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
    c, p, tallies, _nodes = _Parser(tokens, num_sizes).parse_row_with_nodes()
    return c, p, tallies


def parse_row_nodes(text, num_sizes):
    """解析一行指令，返回顶层节点列表（_StitchNode/_GroupNode）。

    与 parse_row_details 走同一条解析路径：任何语法错误（含非法记号、括号
    不匹配、倍数非法、*{...} 项数不符）都以同一 ParseError（同一字符位置）
    抛出，不会返回部分节点。供单行针位预览定位使用。
    """
    if num_sizes < 1:
        raise ValueError("num_sizes 必须为正整数")
    tokens = _tokenize(text)
    if not tokens:
        raise ParseError("该行没有有效指令")
    _c, _p, _tallies, nodes = _Parser(tokens, num_sizes).parse_row_with_nodes()
    return nodes


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
# 单行针位预览：按所选尺码把解析树定位成“实际针法序列”，支持按操作序号
# 随机取一页（最多 PREVIEW_PAGE_SIZE 个）。所有定位只做算术（每个重复段先
# 求一遍长度/消耗，再用除法跳过整段副本），绝不逐个展开重复项，因此
# (k)*1000000000 的尾段也能直接查看。
# ---------------------------------------------------------------------------


class _View(object):
    """所选尺码下定位后的树节点视图。

    kind 为 'stitch'/'group'；children 为定位后的子视图（stitch 时为空）；
    name 为记号名（仅 stitch）；mult 为该节点在所选尺码下的重复倍数；
    copy_length/copy_consume/copy_produce 为“一个副本”（括号内序列或单个
    记号）的实际针法数、消耗与产出针数。
    """

    __slots__ = ('kind', 'name', 'children', 'mult',
                 'copy_length', 'copy_consume', 'copy_produce')

    def __init__(self, kind, mult):
        self.kind = kind
        self.name = None
        self.children = []
        self.mult = mult
        self.copy_length = 0
        self.copy_consume = 0
        self.copy_produce = 0

    @property
    def length(self):
        """整个节点（含 mult 个副本）的实际针法数。

        注意必须用 copy_length（一个副本的针法数）而非 copy_consume：
        yo 不消耗入针，(yo)*3 的针法数是 3 而不是 0。
        """
        return self.copy_length * self.mult

    @property
    def consume(self):
        """整个节点（含 mult 个副本）消耗的入针数。"""
        return self.copy_consume * self.mult

    @property
    def produce(self):
        """整个节点（含 mult 个副本）产出的出针数。"""
        return self.copy_produce * self.mult


def _resolve_view(node, size_index):
    """把解析树节点按所选尺码定位成 _View（整棵树递归，副本不展开）。"""
    mult = node.mults[size_index]
    if isinstance(node, _StitchNode):
        c0, p0 = STITCH_COUNTS[node.name]
        view = _View('stitch', mult)
        view.name = node.name
        view.copy_length = 1
        view.copy_consume = c0
        view.copy_produce = p0
        return view
    view = _View('group', mult)
    children = [_resolve_view(ch, size_index) for ch in node.children]
    view.children = children
    view.copy_length = sum(ch.length for ch in children)
    view.copy_consume = sum(ch.consume for ch in children)
    view.copy_produce = sum(ch.produce for ch in children)
    return view


def _locate_stitch(views, offset):
    """定位偏移 offset（0 起，相对 views 序列起点）所指的实际针法。

    返回 (记号名, 该针法之前已消耗的针数)。每个重复段先算整段长度，再用
    除法一次跳过 offset//length 个完整副本：十亿次重复也只需除法与取模，
    绝不逐个遍历副本。
    """
    consumed = 0
    for view in views:
        if offset >= view.length:
            # 整段未命中：加上该段全部副本的消耗后继续
            offset -= view.length
            consumed += view.consume
            continue
        if view.kind == 'stitch':
            # 命中该记号的第 offset 个副本；前面的副本各消耗 copy_consume
            return view.name, consumed + view.copy_consume * offset
        # 命中括号序列的第 copy 个副本中的第 inner 个针法：整段跳过 copy 个
        # 完整副本（即使该段不消耗入针，也必须用 copy_length 定位副本编号）
        copy, inner = divmod(offset, view.copy_length)
        name, inside = _locate_stitch(view.children, inner)
        return name, consumed + view.copy_consume * copy + inside
    raise IndexError("针法偏移超出范围")


def preview_row_stitches(text, num_sizes, size_index, start):
    """单行针位预览：返回所选尺码下从第 start 个实际针法起的一页。

    start 为 1 起的操作序号；一页最多 PREVIEW_PAGE_SIZE 个。嵌套重复与
    按尺码倍数均按 size_index 指定的尺码定位，跳过未显示的重复段，绝不
    展开整行。非法行抛出与 parse_row_details 相同的 ParseError（同一原文
    字符位置），不给部分预览；start 超出实际针法总数时抛 PreviewStartError。

    返回 dict：
      {"total", "start", "limit", "items", "prev_start", "next_start",
       "consume", "produce"}
    items 中每项为
      {"seq", "stitch", "consume_before", "in_from", "in_to", "out"}
    挂线等零入针针法的 in_from/in_to 为 None。
    """
    if not isinstance(size_index, int) or isinstance(size_index, bool) \
            or not (0 <= size_index < num_sizes):
        raise ValueError("size_index 必须在尺码范围内")
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        raise ValueError("start 必须为正整数")
    nodes = parse_row_nodes(text, num_sizes)
    views = [_resolve_view(node, size_index) for node in nodes]
    total = sum(v.length for v in views)
    if start > total:
        raise PreviewStartError(total)

    end = min(start - 1 + PREVIEW_PAGE_SIZE, total)
    items = []
    for seq in range(start, end + 1):
        name, before = _locate_stitch(views, seq - 1)
        c0, _p0 = STITCH_COUNTS[name]
        items.append({
            "seq": seq,
            "stitch": name,
            "consume_before": before,
            "in_from": None if c0 == 0 else before + 1,
            "in_to": None if c0 == 0 else before + c0,
            # 每个针法恰好产出 1 针，出针序号与操作序号一致
            "out": seq,
        })

    prev_start = max(1, start - PREVIEW_PAGE_SIZE)
    return {
        "total": total,
        "start": start,
        "limit": PREVIEW_PAGE_SIZE,
        "items": items,
        "prev_start": prev_start,
        "next_start": end + 1 if end < total else None,
        "consume": sum(v.consume for v in views),
        "produce": sum(v.produce for v in views),
    }
