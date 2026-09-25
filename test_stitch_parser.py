import unittest

from stitch_parser import (
    PREVIEW_PAGE_SIZE,
    ParseError,
    PreviewStartError,
    parse_row,
    parse_row_details,
    parse_row_nodes,
    parse_row_sizes,
    parse_row_stitch_counts,
    preview_row_stitches,
    reorder_size_multipliers,
    suggest_row_fix,
)


class TestParseRow(unittest.TestCase):
    def test_basic_stitches(self):
        self.assertEqual(parse_row('k'), (1, 1))
        self.assertEqual(parse_row('p'), (1, 1))
        self.assertEqual(parse_row('yo'), (0, 1))
        self.assertEqual(parse_row('k2tog'), (2, 1))

    def test_sequence_with_separators(self):
        self.assertEqual(parse_row('k k yo k2tog'), (4, 4))
        self.assertEqual(parse_row('k, k, yo, k2tog'), (4, 4))
        self.assertEqual(parse_row('kkyo k2tog'), (4, 4))

    def test_group_repeat(self):
        self.assertEqual(parse_row('(k2tog yo)*6'), (12, 12))
        self.assertEqual(parse_row('(k yo)*2 p'), (3, 5))

    def test_nested_groups(self):
        # (k yo)*2 -> (2,4)；+ k2tog -> (4,5)；整体 *3
        self.assertEqual(parse_row('((k yo)*2 k2tog)*3'), (12, 15))

    def test_atom_repeat(self):
        self.assertEqual(parse_row('k*3'), (3, 3))
        self.assertEqual(parse_row('k2tog*4'), (8, 4))

    def test_huge_multiplier_is_not_expanded(self):
        self.assertEqual(parse_row('(k yo)*1000000000'), (1000000000, 2000000000))

    def test_case_insensitive(self):
        self.assertEqual(parse_row('K2TOG, YO, K, P'), (4, 4))

    def test_invalid_tokens(self):
        for bad in ['ssk', 'k3', 'k 2', 'm1', 'k/p', 'k+yo']:
            with self.assertRaises(ParseError, msg=bad):
                parse_row(bad)

    def test_bad_parentheses(self):
        for bad in ['(k yo', 'k)', '()', '( )', '(k))*2', '(k (yo)']:
            with self.assertRaises(ParseError, msg=bad):
                parse_row(bad)

    def test_bad_multiplier(self):
        for bad in ['k*0', '(k)*0', 'k*-1', 'k*', 'k*yo', '*3 k', '(k)*2*3']:
            with self.assertRaises(ParseError, msg=bad):
                parse_row(bad)

    def test_empty_row(self):
        with self.assertRaises(ParseError):
            parse_row('   ')


class TestParseRowSizes(unittest.TestCase):
    def test_plain_repeat_applies_to_all_sizes(self):
        c, p = parse_row_sizes('k*3', 3)
        self.assertEqual(c, [3, 3, 3])
        self.assertEqual(p, [3, 3, 3])

    def test_size_repeat_on_atom(self):
        c, p = parse_row_sizes('k*{6|8|10}', 3)
        self.assertEqual(c, [6, 8, 10])
        self.assertEqual(p, [6, 8, 10])

    def test_size_repeat_on_group(self):
        c, p = parse_row_sizes('(k2tog yo)*{6|8|10}', 3)
        self.assertEqual(c, [12, 16, 20])
        self.assertEqual(p, [12, 16, 20])

    def test_size_repeat_with_spaces(self):
        c, p = parse_row_sizes('k*{6 | 8 | 10}', 3)
        self.assertEqual(c, [6, 8, 10])
        self.assertEqual(p, [6, 8, 10])

    def test_nested_mixed_repeats(self):
        # k*{2|3} -> c [2,3] p [2,3]；+ yo -> c [2,3] p [3,4]；整体 *2
        c, p = parse_row_sizes('(k*{2|3} yo)*2', 2)
        self.assertEqual(c, [4, 6])
        self.assertEqual(p, [6, 8])

    def test_nested_group_size_repeat_outside(self):
        # (k yo)*2 -> c 2 p 4；整体 *{2|3}
        c, p = parse_row_sizes('((k yo)*2)*{2|3}', 2)
        self.assertEqual(c, [4, 6])
        self.assertEqual(p, [8, 12])

    def test_single_size_brace(self):
        c, p = parse_row_sizes('k*{5}', 1)
        self.assertEqual((c, p), ([5], [5]))
        self.assertEqual(parse_row('k*{5}'), (5, 5))

    def test_count_mismatch_reports_position(self):
        with self.assertRaises(ParseError) as ctx:
            parse_row_sizes('kp*{6|8}', 3)
        self.assertIn('项数不符', str(ctx.exception))
        self.assertEqual(ctx.exception.pos, 2)  # '*' 的下标

    def test_bad_size_repeat(self):
        for bad in ['k*{0|2}', 'k*{2|}', 'k*{|2}', 'k*{}', 'k*{2 3}',
                    'k*{2|3', 'k*{2|3}*2', 'k*{a|b}', 'k*2|3']:
            with self.assertRaises(ParseError, msg=bad):
                parse_row_sizes(bad, 2)

    def test_huge_size_multiplier_is_not_expanded(self):
        c, p = parse_row_sizes('(k yo)*{1000000000|1}', 2)
        self.assertEqual(c, [1000000000, 1])
        self.assertEqual(p, [2000000000, 2])


class TestParseRowStitchCounts(unittest.TestCase):
    def test_basic_stitch_counts(self):
        tallies = parse_row_stitch_counts('k p yo k2tog', 1)
        self.assertEqual(tallies['k'], [1])
        self.assertEqual(tallies['p'], [1])
        self.assertEqual(tallies['yo'], [1])
        self.assertEqual(tallies['k2tog'], [1])

    def test_repeat_multiplies_counts(self):
        tallies = parse_row_stitch_counts('(k2tog yo)*6', 1)
        self.assertEqual(tallies['k2tog'], [6])
        self.assertEqual(tallies['yo'], [6])
        self.assertEqual(tallies['k'], [0])

    def test_nested_groups(self):
        # ((k yo)*2 k2tog)*3：k=6, yo=6, k2tog=3
        tallies = parse_row_stitch_counts('((k yo)*2 k2tog)*3', 1)
        self.assertEqual(tallies['k'], [6])
        self.assertEqual(tallies['yo'], [6])
        self.assertEqual(tallies['k2tog'], [3])

    def test_size_repeats_counted_per_size(self):
        tallies = parse_row_stitch_counts('(k2tog yo)*{6|8|10}', 3)
        self.assertEqual(tallies['k2tog'], [6, 8, 10])
        self.assertEqual(tallies['yo'], [6, 8, 10])

    def test_nested_mixed_repeats(self):
        # (k*{2|3} yo)*2：k=[4,6]，yo=[2,2]
        tallies = parse_row_stitch_counts('(k*{2|3} yo)*2', 2)
        self.assertEqual(tallies['k'], [4, 6])
        self.assertEqual(tallies['yo'], [2, 2])
        self.assertEqual(tallies['p'], [0, 0])
        self.assertEqual(tallies['k2tog'], [0, 0])

    def test_atom_size_repeat(self):
        tallies = parse_row_stitch_counts('k*{6|8|10}', 3)
        self.assertEqual(tallies['k'], [6, 8, 10])

    def test_huge_multiplier_count_not_expanded(self):
        tallies = parse_row_stitch_counts('(k yo)*1000000000', 1)
        self.assertEqual(tallies['k'], [1000000000])
        self.assertEqual(tallies['yo'], [1000000000])

    def test_details_matches_counts(self):
        c, p, tallies = parse_row_details('((k yo)*2 k2tog)*3', 1)
        self.assertEqual(c, [12])
        self.assertEqual(p, [15])
        self.assertEqual(tallies['k2tog'], [3])

    def test_invalid_row_raises(self):
        with self.assertRaises(ParseError):
            parse_row_stitch_counts('ssk', 1)


class TestSuggestRowFix(unittest.TestCase):
    def test_size_repeat_single_item_fix(self):
        # L 需要 10 而非 9；S/M 原本正确，不能受影响
        sug = suggest_row_fix('(k2tog yo)*{6|8|9}', [12, 16, 20])
        self.assertIsNotNone(sug)
        self.assertEqual(sug['instruction'], '(k2tog yo)*{6|8|10}')
        self.assertEqual(sug['old'], '*{6|8|9}')
        self.assertEqual(sug['new'], '*{6|8|10}')
        self.assertEqual(sug['char'], 11)  # '*' 是第 11 个字符
        self.assertEqual(sug['produces'], [12, 16, 20])

    def test_plain_repeat_same_value_for_all_sizes(self):
        sug = suggest_row_fix('k*12', [10, 10])
        self.assertEqual(sug['instruction'], 'k*10')
        self.assertEqual(sug['old'], '*12')
        self.assertEqual(sug['new'], '*10')

    def test_plain_repeat_becomes_size_repeat(self):
        # 各尺码需要不同倍数时，普通 *n 改写为 *{a|b|c}
        sug = suggest_row_fix('(k2tog yo)*6', [12, 14, 16])
        self.assertEqual(sug['instruction'], '(k2tog yo)*{6|7|8}')
        self.assertEqual(sug['produces'], [12, 14, 16])

    def test_size_repeat_collapses_to_plain_when_uniform(self):
        # 两个尺码都需要 3：*{2|2} 改写为 *3；另一处倍数无法整除，不可行
        sug = suggest_row_fix('(k2tog)*{3|4} k*{2|2}', [9, 11])
        self.assertEqual(sug['instruction'], '(k2tog)*{3|4} k*3')
        self.assertEqual(sug['produces'], [6, 7])

    def test_nested_repeat_fixed_by_arithmetic(self):
        # ((k yo)*2 k2tog)*3 消耗 12；可用 15 时内层 *2 -> *3（每组多 1 针，外层 *3）
        sug = suggest_row_fix('((k yo)*2 k2tog)*3', [15])
        self.assertEqual(sug['instruction'], '((k yo)*3 k2tog)*3')
        self.assertEqual(sug['produces'], [21])

    def test_no_solution_when_not_divisible(self):
        # (k2tog)*n 消耗总是偶数，可用 7 针无法通过改倍数修正
        self.assertIsNone(suggest_row_fix('(k2tog)*3', [7]))

    def test_no_solution_when_multiplier_would_be_non_positive(self):
        # k*2 之外的基数已是 6 针，可用 3 针：倍数只能为非正数，无解
        self.assertIsNone(suggest_row_fix('k*2 (k2tog)*3', [3]))

    def test_zero_consume_repeat_cannot_fix(self):
        # (yo)*n 不消耗针数，无法用它修正差额；只能改 k*3
        sug = suggest_row_fix('(yo)*5 k*3', [4])
        self.assertEqual(sug['instruction'], '(yo)*5 k*4')

    def test_inactive_sizes_keep_current_values(self):
        # M 已停止推算（None）：倍数保持 8，只修 L 的 9 -> 10
        sug = suggest_row_fix('(k2tog yo)*{6|8|9}', [12, None, 20])
        self.assertEqual(sug['instruction'], '(k2tog yo)*{6|8|10}')

    def test_smallest_delta_wins(self):
        # 两个位置都可修：(k2tog)*2 -> *3（变化 1）优于 k*2 -> k*4（变化 2）
        sug = suggest_row_fix('(k2tog)*2 k*2', [8])
        self.assertEqual(sug['instruction'], '(k2tog)*3 k*2')

    def test_position_breaks_tie(self):
        # 两个 k*2 都需改为 k*4（变化量相同），取原文位置靠前者
        sug = suggest_row_fix('k*2 k*2', [6])
        self.assertEqual(sug['instruction'], 'k*4 k*2')

    def test_unparseable_row_returns_none(self):
        self.assertIsNone(suggest_row_fix('ssk', [4]))

    def test_huge_multiplier_not_expanded(self):
        sug = suggest_row_fix('(k yo)*1000000000', [1000000001])
        self.assertEqual(sug['instruction'], '(k yo)*1000000001')


class TestReorderSizeMultipliers(unittest.TestCase):
    def test_swap_first_two(self):
        # order [1,0,2]：原 [A,B,C] 的新顺序为 [B,A,C]
        self.assertEqual(
            reorder_size_multipliers('(k2tog yo)*{6|8|10}', [1, 0, 2]),
            '(k2tog yo)*{8|6|10}')

    def test_move_first_down(self):
        # order [2,0,1]：[A,B,C] -> [C,A,B]
        self.assertEqual(
            reorder_size_multipliers('k*{6|8|10}', [2, 0, 1]),
            'k*{10|6|8}')

    def test_multiple_sites_on_one_line(self):
        # 同一行多处 *{...} 都要重排
        self.assertEqual(
            reorder_size_multipliers('k*{1|2|3} p*{4|5|6}', [2, 1, 0]),
            'k*{3|2|1} p*{6|5|4}')

    def test_nested_repeats_reordered(self):
        # 内层按尺码倍数随尺码重排，外层普通 *2 不变
        self.assertEqual(
            reorder_size_multipliers('(k*{2|3|4} yo)*2', [1, 0, 2]),
            '(k*{3|2|4} yo)*2')

    def test_plain_repeat_and_other_text_unchanged(self):
        text = '((k yo)*2 k2tog)*3 ssk*5'
        self.assertEqual(reorder_size_multipliers(text, [2, 0, 1]), text)

    def test_spaces_and_separators_preserved(self):
        self.assertEqual(
            reorder_size_multipliers('k*{ 6 | 8 | 10 }', [2, 0, 1]),
            'k*{ 10 | 6 | 8 }')

    def test_number_text_moved_as_is(self):
        # 前导零等数字原文随尺码整体搬移，不重新规范化
        self.assertEqual(
            reorder_size_multipliers('k*{06|8|010}', [2, 1, 0]),
            'k*{010|8|06}')

    def test_line_without_size_repeat_is_identical(self):
        text = 'plain line without any brace k p yo'
        self.assertIs(reorder_size_multipliers(text, [2, 0, 1]) == text, True)

    def test_single_size_swap_is_identity(self):
        self.assertEqual(reorder_size_multipliers('k*{5} k*2', [0]), 'k*{5} k*2')

    def test_count_mismatch_reports_star_position(self):
        with self.assertRaises(ParseError) as ctx:
            reorder_size_multipliers('k p*{6|8}', [2, 1, 0])
        self.assertIn('项数不符', str(ctx.exception))
        self.assertEqual(ctx.exception.pos, 3)  # '*' 的下标

    def test_invalid_values_report_position(self):
        cases = [
            ('k*{0|2|3}', 3),    # 0 不是正整数，指向 '0'
            ('k*{1|}', 5),       # 缺值，指向 '}'
            ('k*{}', 3),         # 空括号，指向 '}'
            ('k*{1|2', 2),       # 未闭合，指向 '{'
            ('k*{1|a|3}', 5),    # 非法字符，指向 'a'
            ('k*{1 2|3}', 5),    # 缺少 '|'，指向空格后的数字位置
        ]
        for text, pos in cases:
            with self.assertRaises(ParseError, msg=text) as ctx:
                reorder_size_multipliers(text, [2, 1, 0])
            self.assertEqual(ctx.exception.pos, pos, msg=text)

    def test_error_aborts_whole_line(self):
        # 第一处合法、第二处非法时不返回部分重排结果
        with self.assertRaises(ParseError):
            reorder_size_multipliers('k*{1|2|3} k*{1|2}', [2, 1, 0])

    def test_reordered_row_matches_permuted_parse(self):
        # 重排后第 i 个尺码的针数 == 重排前第 order[i] 个尺码的针数
        text = '(k2tog yo)*{6|8|10} p*{12|16|20} (k*{1|2|3})*{2|3|4}'
        order = [2, 0, 1]
        new_text = reorder_size_multipliers(text, order)
        c_old, p_old = parse_row_sizes(text, 3)
        c_new, p_new = parse_row_sizes(new_text, 3)
        self.assertEqual(c_new, [c_old[i] for i in order])
        self.assertEqual(p_new, [p_old[i] for i in order])

    def test_invalid_order_raises_value_error(self):
        with self.assertRaises(ValueError):
            reorder_size_multipliers('k*{1|2|3}', [0, 0, 1])


def _expand_row(text, num_sizes, size_index):
    """按尺码真正展开（仅测试辅助，不考虑性能），返回实际针法名序列。"""
    names = []

    def walk(nodes, copies):
        for node in nodes:
            reps = copies * node.mults[size_index]
            if hasattr(node, 'name'):  # _StitchNode
                names.extend([node.name] * reps)
            else:
                for _ in range(reps):
                    walk(node.children, 1)

    walk(parse_row_nodes(text, num_sizes), 1)
    return names


def _expected_items(text, num_sizes, size_index):
    """逐个针法模拟：(seq, name, consume_before)。"""
    before = 0
    expected = []
    for i, name in enumerate(_expand_row(text, num_sizes, size_index), 1):
        consume = {'k': 1, 'p': 1, 'yo': 0, 'k2tog': 2}[name]
        expected.append((i, name, before))
        before += consume
    return expected


class TestPreviewRowStitches(unittest.TestCase):
    def _all_pages(self, text, num_sizes, size_index):
        """翻完所有页，拼接 (seq, name, consume_before)，返回 (末页, 序列)。"""
        start = 1
        seen = []
        page = None
        while True:
            page = preview_row_stitches(text, num_sizes, size_index, start)
            for item in page['items']:
                seen.append((item['seq'], item['stitch'],
                             item['consume_before']))
            if page['next_start'] is None:
                break
            start = page['next_start']
        return page, seen

    def test_basic_row_items_and_ranges(self):
        page = preview_row_stitches('k p yo k2tog', 1, 0, 1)
        self.assertEqual(page['total'], 4)
        self.assertIsNone(page['next_start'])
        self.assertEqual(
            [(i['seq'], i['stitch'], i['in_from'], i['in_to'], i['out'])
             for i in page['items']],
            [(1, 'k', 1, 1, 1),
             (2, 'p', 2, 2, 2),
             (3, 'yo', None, None, 3),
             (4, 'k2tog', 3, 4, 4)])
        self.assertEqual([i['consume_before'] for i in page['items']],
                         [0, 1, 2, 2])

    def test_repeat_group_ranges(self):
        page = preview_row_stitches('(k2tog yo)*2', 1, 0, 1)
        self.assertEqual(
            [(i['seq'], i['stitch'], i['in_from'], i['in_to'])
             for i in page['items']],
            [(1, 'k2tog', 1, 2), (2, 'yo', None, None),
             (3, 'k2tog', 3, 4), (4, 'yo', None, None)])

    def test_nested_repeats(self):
        # ((k yo)*2 k2tog)*3 -> k yo k yo k2tog ×3，共 15 个针法
        page = preview_row_stitches('((k yo)*2 k2tog)*3', 1, 0, 1)
        self.assertEqual(page['total'], 15)
        self.assertEqual([i['stitch'] for i in page['items']],
                         (['k', 'yo', 'k', 'yo', 'k2tog']) * 3)
        # 第二组第一个 k 之前已消耗 4 针（k + k2tog：1+2，再加一个 k 共4）
        self.assertEqual(page['items'][5]['in_from'], 5)
        self.assertIsNone(page['next_start'])

    def test_size_multipliers_resolved_per_size(self):
        text = '(k2tog yo)*{6|8|10}'
        for size_index, total in enumerate([12, 16, 20]):
            page = preview_row_stitches(text, 3, size_index, 1)
            self.assertEqual(page['total'], total)
            self.assertEqual(page['consume'], total)
            self.assertEqual(page['produce'], total)
        tail = preview_row_stitches(text, 3, 2, 19)
        self.assertEqual(
            [(i['seq'], i['stitch'], i['in_from']) for i in tail['items']],
            [(19, 'k2tog', 19), (20, 'yo', None)])

    def test_nested_size_multiplier(self):
        # (k*{2|3} yo)*2：S 共 6 个，M 共 8 个
        page = preview_row_stitches('(k*{2|3} yo)*2', 2, 1, 1)
        self.assertEqual(
            [i['stitch'] for i in page['items']],
            ['k', 'k', 'k', 'yo', 'k', 'k', 'k', 'yo'])

    def test_zero_consume_yo_repeats(self):
        # 回归：(yo)*3 共 3 个针法，不能因不消耗入针而坍缩成 1 个
        page = preview_row_stitches('(yo)*3', 1, 0, 1)
        self.assertEqual(page['total'], 3)
        self.assertEqual([i['seq'] for i in page['items']], [1, 2, 3])
        self.assertTrue(all(i['stitch'] == 'yo' for i in page['items']))
        self.assertTrue(all(i['in_from'] is None for i in page['items']))
        self.assertEqual([i['consume_before'] for i in page['items']],
                         [0, 0, 0])

    def test_huge_zero_consume_tail(self):
        page = preview_row_stitches('(yo)*1000000000', 1, 0, 999999998)
        self.assertEqual(page['total'], 1000000000)
        self.assertEqual([i['seq'] for i in page['items']],
                         [999999998, 999999999, 1000000000])

    def test_zero_consume_group_mixed(self):
        # (yo k)*2 -> yo k yo k：挂线标明插在第 0/1 针之后
        page = preview_row_stitches('(yo k)*2', 1, 0, 1)
        self.assertEqual(
            [(i['seq'], i['stitch'], i['consume_before'], i['in_from'])
             for i in page['items']],
            [(1, 'yo', 0, None), (2, 'k', 0, 1),
             (3, 'yo', 1, None), (4, 'k', 1, 2)])

    def test_deep_nested_huge_tail(self):
        # (((k yo)*1e6)*1e6)：2e12 个针法，直接取尾段
        page = preview_row_stitches(
            '(((k yo)*1000000)*1000000)', 1, 0, 2000000000000 - 1)
        self.assertEqual(page['total'], 2000000000000)
        self.assertEqual([(i['seq'], i['stitch']) for i in page['items']],
                         [(1999999999999, 'k'), (2000000000000, 'yo')])

    def test_pagination_page_size_and_navigation(self):
        self.assertEqual(PREVIEW_PAGE_SIZE, 50)
        first = preview_row_stitches('k*120', 1, 0, 1)
        self.assertEqual(len(first['items']), 50)
        self.assertEqual(first['items'][0]['seq'], 1)
        self.assertEqual(first['items'][-1]['seq'], 50)
        self.assertEqual(first['prev_start'], 1)
        self.assertEqual(first['next_start'], 51)
        second = preview_row_stitches('k*120', 1, 0, 51)
        self.assertEqual(len(second['items']), 50)
        self.assertEqual(second['prev_start'], 1)
        third = preview_row_stitches('k*120', 1, 0, 101)
        self.assertEqual(len(third['items']), 20)
        self.assertIsNone(third['next_start'])
        self.assertEqual(third['items'][-1]['seq'], 120)

    def test_start_in_middle_of_page(self):
        # 从非整页边界开始：本页 50 个，上一页起点回退 50
        page = preview_row_stitches('k*200', 1, 0, 60)
        self.assertEqual([i['seq'] for i in page['items']], list(range(60, 110)))
        self.assertEqual(page['prev_start'], 10)
        self.assertEqual(page['next_start'], 110)

    def test_paging_covers_every_stitch(self):
        cases = [
            ('k p yo k2tog', 1, 0),
            ('(k2tog yo)*6', 1, 0),
            ('((k yo)*2 k2tog)*3', 1, 0),
            ('(k2tog yo)*{6|8|10}', 3, 0),
            ('(k2tog yo)*{6|8|10}', 3, 1),
            ('(k2tog yo)*{6|8|10}', 3, 2),
            ('((k yo)*{2|3|4} p)*{3|2|1}', 3, 2),
            ('k2tog*60 yo (p)*60', 1, 0),
            ('(yo)*5 k*3', 1, 0),
            ('(yo)*60 (k)*60', 1, 0),
            ('(yo k)*100', 1, 0),
        ]
        for text, nsz, si in cases:
            page, seen = self._all_pages(text, nsz, si)
            self.assertEqual(seen, _expected_items(text, nsz, si), msg=text)
            self.assertEqual(page['consume'],
                             parse_row_sizes(text, nsz)[0][si])
            self.assertEqual(page['produce'],
                             parse_row_sizes(text, nsz)[1][si])

    def test_each_items_in_out_ranges(self):
        # 非挂线针法入针范围宽度与其消耗一致；每个针法出针序号等于操作序号
        text = '((k2tog)*2 yo)*2 (p yo k)*2'
        page = preview_row_stitches(text, 1, 0, 1)
        for item in page['items']:
            self.assertEqual(item['out'], item['seq'])
            if item['stitch'] == 'yo':
                self.assertIsNone(item['in_from'])
                self.assertIsNone(item['in_to'])
            else:
                width = item['in_to'] - item['in_from'] + 1
                self.assertEqual(width, {'k': 1, 'p': 1,
                                         'k2tog': 2}[item['stitch']])

    def test_huge_repeat_tail_without_expansion(self):
        page = preview_row_stitches('(k yo)*1000000000', 1, 0, 1999999997)
        self.assertEqual(page['total'], 2000000000)
        self.assertEqual(
            [(i['seq'], i['stitch'], i['in_from']) for i in page['items']],
            [(1999999997, 'k', 999999999),
             (1999999998, 'yo', None),
             (1999999999, 'k', 1000000000),
             (2000000000, 'yo', None)])

    def test_huge_size_multiplier_tail(self):
        page = preview_row_stitches('k2tog*{1000000000|2}', 2, 0, 999999999)
        self.assertEqual(page['total'], 1000000000)
        self.assertEqual((page['items'][0]['in_from'],
                          page['items'][0]['in_to']),
                         (1999999997, 1999999998))

    def test_huge_repeat_pagination(self):
        first = preview_row_stitches('k*1000000000', 1, 0, 1)
        self.assertEqual(first['next_start'], 51)
        tail_start = 1000000000 - 49
        tail = preview_row_stitches('k*1000000000', 1, 0, tail_start)
        self.assertEqual(len(tail['items']), 50)
        self.assertIsNone(tail['next_start'])
        self.assertEqual(tail['items'][0]['in_from'], tail_start)
        self.assertEqual(tail['items'][-1]['out'], 1000000000)
        self.assertEqual(tail['prev_start'], tail_start - 50)

    def test_start_beyond_total_raises(self):
        with self.assertRaises(PreviewStartError) as ctx:
            preview_row_stitches('k*3', 1, 0, 4)
        self.assertEqual(ctx.exception.total, 3)

    def test_invalid_start_raises_value_error(self):
        for bad in [0, -1, '1', 1.0, True, None]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                preview_row_stitches('k', 1, 0, bad)

    def test_invalid_size_index_raises_value_error(self):
        for bad in [-1, 2, 3, '0', 0.0, True]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                preview_row_stitches('k*{1|2}', 2, bad, 1)

    def test_parse_error_is_same_error_and_position(self):
        # 非法行返回与 parse_row_details 相同的 ParseError 与字符位置
        for bad in ['ssk', '(k yo', 'k)', '()', 'k*0', 'k*{1}']:
            with self.assertRaises(ParseError, msg=bad) as ctx_preview:
                preview_row_stitches(bad, 2, 0, 1)
            with self.assertRaises(ParseError) as ctx_parse:
                parse_row_details(bad, 2)
            self.assertEqual(str(ctx_preview.exception),
                             str(ctx_parse.exception), msg=bad)
            self.assertEqual(ctx_preview.exception.pos,
                             ctx_parse.exception.pos, msg=bad)

    def test_empty_row_is_parse_error(self):
        with self.assertRaises(ParseError):
            preview_row_stitches('   ', 1, 0, 1)

    def test_node_tree_arithmetic_matches_details(self):
        # ((k yo)*2 k2tog)*3：每组消耗4产出5，*3 -> 消耗12产出15；
        # k*{2|3} -> 消耗/产出各 +2/+3
        text = '((k yo)*2 k2tog)*3 k*{2|3}'
        nodes = parse_row_nodes(text, 2)
        c, p, tallies = parse_row_details(text, 2)
        self.assertEqual((c, p), ([14, 15], [17, 18]))
        self.assertEqual(len(nodes), 2)
        self.assertEqual(tallies['k'], [8, 9])


if __name__ == '__main__':
    unittest.main()
