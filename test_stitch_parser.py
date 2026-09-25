import unittest

from stitch_parser import (
    ParseError,
    parse_row,
    parse_row_details,
    parse_row_sizes,
    parse_row_stitch_counts,
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


if __name__ == '__main__':
    unittest.main()
