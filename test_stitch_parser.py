import unittest

from stitch_parser import (
    PREVIEW_PAGE_SIZE,
    ParseError,
    build_stitch_plan,
    parse_row,
    parse_row_details,
    parse_row_sizes,
    parse_row_stitch_counts,
    plan_stitch_page,
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


class TestStitchPreview(unittest.TestCase):
    def test_totals_match_parse(self):
        plan = build_stitch_plan('(k2tog yo)*{6|8|10}', 3, 1)
        consumes, produces = parse_row_sizes('(k2tog yo)*{6|8|10}', 3)
        self.assertEqual(plan.total_stitches, 16)
        self.assertEqual(plan.total_consume, consumes[1])
        self.assertEqual(plan.total_produce, produces[1])

    def test_first_page_basic_ranges(self):
        plan = build_stitch_plan('k2tog yo', 1, 0)
        page = plan_stitch_page(plan, 1)
        self.assertEqual(len(page), 2)
        self.assertEqual(page[0], {
            'seq': 1, 'stitch': 'k2tog',
            'in_start': 1, 'in_end': 2, 'out_start': 1, 'out_end': 1,
            'insert_after': None})
        self.assertEqual(page[1], {
            'seq': 2, 'stitch': 'yo',
            'in_start': None, 'in_end': None, 'out_start': 2, 'out_end': 2,
            'insert_after': 2})

    def test_yo_first_inserts_before_first_needle(self):
        plan = build_stitch_plan('yo k', 1, 0)
        page = plan_stitch_page(plan, 1)
        self.assertEqual(page[0]['stitch'], 'yo')
        self.assertIsNone(page[0]['in_start'])
        self.assertEqual(page[0]['insert_after'], 0)  # 插入第 1 针之前
        self.assertEqual(page[0]['out_start'], 1)
        self.assertEqual(page[1], {
            'seq': 2, 'stitch': 'k',
            'in_start': 1, 'in_end': 1, 'out_start': 2, 'out_end': 2,
            'insert_after': None})

    def test_repeat_ranges_walk_forward(self):
        # (k2tog yo)*3：针法序列 k2tog,yo ×3，入针/出针逐次推进
        plan = build_stitch_plan('(k2tog yo)*3', 1, 0)
        self.assertEqual(plan.total_stitches, 6)
        page = plan_stitch_page(plan, 1)
        self.assertEqual([e['seq'] for e in page], [1, 2, 3, 4, 5, 6])
        self.assertEqual([e['stitch'] for e in page],
                         ['k2tog', 'yo'] * 3)
        # 第 2 组 k2tog 消耗 3-4，出针 3；yo 插在 4 之后
        self.assertEqual((page[2]['in_start'], page[2]['in_end']), (3, 4))
        self.assertEqual(page[2]['out_start'], 3)
        self.assertEqual(page[3]['insert_after'], 4)
        self.assertEqual(page[3]['out_start'], 4)
        # 第 3 组 yo 插在 6 之后，最后产出 6 针
        self.assertEqual(page[5]['insert_after'], 6)
        self.assertEqual(page[5]['out_end'], 6)

    def test_size_repeat_locates_selected_size(self):
        text = '(k2tog yo)*{6|8|10}'
        plan_s = build_stitch_plan(text, 3, 0)
        plan_l = build_stitch_plan(text, 3, 2)
        self.assertEqual(plan_s.total_stitches, 12)
        self.assertEqual(plan_l.total_stitches, 20)
        # L 尺码尾段：最后两个针法为第 10 组 k2tog + yo（前 9 组消耗 18）
        tail = plan_stitch_page(plan_l, 19)
        self.assertEqual(len(tail), 2)
        self.assertEqual(tail[0]['stitch'], 'k2tog')
        self.assertEqual((tail[0]['in_start'], tail[0]['in_end']), (19, 20))
        self.assertEqual(tail[0]['out_start'], 19)
        self.assertEqual(tail[1]['stitch'], 'yo')
        self.assertEqual(tail[1]['insert_after'], 20)
        self.assertEqual(tail[1]['out_start'], 20)

    def test_nested_repeats_walk_in_order(self):
        # ((k yo)*2 k2tog)*2 每组实际针法：k,yo,k,yo,k2tog
        plan = build_stitch_plan('((k yo)*2 k2tog)*2', 1, 0)
        self.assertEqual(plan.total_stitches, 10)
        page = plan_stitch_page(plan, 1, 10)
        names = [e['stitch'] for e in page]
        self.assertEqual(names, ['k', 'yo', 'k', 'yo', 'k2tog'] * 2)
        # 第 2 组起始（seq 6 的 k）：前一组消耗 4、产出 5
        self.assertEqual(page[5]['seq'], 6)
        self.assertEqual(page[5]['in_start'], 5)
        self.assertEqual(page[5]['out_start'], 6)
        # 第 2 组 k2tog 前已消耗 8
        self.assertIsNone(page[9]['insert_after'])
        self.assertEqual((page[9]['in_start'], page[9]['in_end']), (7, 8))
        self.assertEqual(page[9]['out_start'], 10)

    def test_nested_size_and_plain_repeats(self):
        # (k*{2|3} yo)*2：S 每组 k,k,yo，共 6 个针法
        plan = build_stitch_plan('(k*{2|3} yo)*2', 2, 0)
        page = plan_stitch_page(plan, 1, 10)
        self.assertEqual([e['stitch'] for e in page], ['k', 'k', 'yo'] * 2)
        self.assertEqual(plan.total_consume, 4)
        self.assertEqual(plan.total_produce, 6)
        # M：每组 k,k,k,yo，共 8 个针法
        plan_m = build_stitch_plan('(k*{2|3} yo)*2', 2, 1)
        self.assertEqual(plan_m.total_stitches, 8)
        self.assertEqual(plan_m.total_consume, 6)

    def test_paging_covers_every_stitch_without_expanding(self):
        plan = build_stitch_plan('(k2tog yo)*63 k*2', 1, 0)  # 128 个针法
        self.assertEqual(plan.total_stitches, 128)
        seen = []
        start = 1
        while True:
            page = plan_stitch_page(plan, start)
            self.assertLessEqual(len(page), PREVIEW_PAGE_SIZE)
            seen.extend(page)
            start += len(page)
            if len(page) < PREVIEW_PAGE_SIZE:
                break
        self.assertEqual(len(seen), 128)
        self.assertEqual([e['seq'] for e in seen], list(range(1, 129)))
        # 末两个为 k
        self.assertEqual(seen[-2]['stitch'], 'k')
        self.assertEqual(seen[-1]['stitch'], 'k')

    def test_page_starting_mid_repeat(self):
        # 从重复段内部第 5 个针法起：(k2tog*2 yo)*4
        plan = build_stitch_plan('(k2tog*2 yo)*4', 1, 0)
        page = plan_stitch_page(plan, 5, 2)
        self.assertEqual(len(page), 2)
        self.assertEqual(page[0]['seq'], 5)
        self.assertEqual(page[0]['stitch'], 'k2tog')
        self.assertEqual((page[0]['in_start'], page[0]['in_end']), (7, 8))
        self.assertEqual(page[0]['out_start'], 5)
        self.assertEqual(page[1]['stitch'], 'yo')
        self.assertEqual(page[1]['insert_after'], 8)

    def test_huge_repeat_tail_viewed_directly(self):
        plan = build_stitch_plan('(k yo k2tog)*1000000000', 1, 0)
        self.assertEqual(plan.total_stitches, 3000000000)
        self.assertEqual(plan.total_consume, 3000000000)
        self.assertEqual(plan.total_produce, 3000000000)
        page = plan_stitch_page(plan, 2999999999)  # 最后两个针法
        self.assertEqual(len(page), 2)
        self.assertEqual(page[0]['stitch'], 'yo')
        self.assertEqual(page[0]['insert_after'], 2999999998)
        self.assertEqual(page[0]['out_start'], 2999999999)
        self.assertEqual(page[1]['stitch'], 'k2tog')
        self.assertEqual((page[1]['in_start'], page[1]['in_end']),
                         (2999999999, 3000000000))
        self.assertEqual(page[1]['out_start'], 3000000000)

    def test_huge_size_repeat_tail(self):
        plan = build_stitch_plan('(k2tog yo)*{1000000000|1}', 2, 0)
        page = plan_stitch_page(plan, 2000000000)
        self.assertEqual(len(page), 1)
        self.assertEqual(page[0]['stitch'], 'yo')
        self.assertEqual(page[0]['insert_after'], 2000000000)
        self.assertEqual(page[0]['out_start'], 2000000000)

    def test_huge_nested_repeat_tail(self):
        # ((k yo)*1000000000 k2tog)*3：尾段 k2tog
        plan = build_stitch_plan('((k yo)*1000000000 k2tog)*3', 1, 0)
        self.assertEqual(plan.total_stitches, 6000000003)
        page = plan_stitch_page(plan, plan.total_stitches)
        self.assertEqual(len(page), 1)
        self.assertEqual(page[0]['stitch'], 'k2tog')
        self.assertEqual((page[0]['in_start'], page[0]['in_end']),
                         (3000000005, 3000000006))
        self.assertEqual(page[0]['out_start'], 6000000003)

    def test_start_validation(self):
        plan = build_stitch_plan('k*3', 1, 0)
        for bad in [0, -1, 4, '2', 1.5, True, None]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                plan_stitch_page(plan, bad)

    def test_invalid_row_raises_parse_error_with_position(self):
        with self.assertRaises(ParseError) as ctx:
            build_stitch_plan('ssk', 1, 0)
        self.assertEqual(ctx.exception.pos, 0)
        with self.assertRaises(ParseError) as ctx:
            build_stitch_plan('k*{6|8}', 3, 0)  # 项数与尺码数不符
        self.assertIn('项数不符', str(ctx.exception))
        self.assertEqual(ctx.exception.pos, 1)

    def test_invalid_size_index(self):
        with self.assertRaises(ValueError):
            build_stitch_plan('k', 1, 1)
        with self.assertRaises(ValueError):
            build_stitch_plan('k', 1, -1)

    def test_yo_only_row(self):
        plan = build_stitch_plan('yo*4', 1, 0)
        self.assertEqual(plan.total_consume, 0)
        self.assertEqual(plan.total_produce, 4)
        page = plan_stitch_page(plan, 2, 2)
        self.assertEqual([e['stitch'] for e in page], ['yo', 'yo'])
        self.assertEqual([e['insert_after'] for e in page], [0, 0])
        self.assertEqual([e['out_start'] for e in page], [2, 3])


if __name__ == '__main__':
    unittest.main()
