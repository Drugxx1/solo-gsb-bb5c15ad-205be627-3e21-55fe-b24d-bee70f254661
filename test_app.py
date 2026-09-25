import unittest

from app import app


class TestValidateApi(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def post(self, cast_on, pattern):
        return self.client.post('/api/validate', json={
            'cast_on': cast_on,
            'pattern': pattern,
        }).get_json()

    def test_all_valid(self):
        data = self.post(12, '(k2tog yo)*6\np*12\n(k2tog)*6\n(yo k2tog)*3\nk*6')
        self.assertTrue(data['ok'])
        self.assertIsNone(data['error'])
        self.assertEqual(len(data['rows']), 5)
        self.assertEqual(data['rows'][0]['in'], 12)
        self.assertEqual(data['rows'][0]['consume'], 12)
        self.assertEqual(data['rows'][0]['out'], 12)
        self.assertEqual(data['rows'][2]['out'], 6)
        self.assertEqual(data['final'], 6)

    def test_count_shortfall(self):
        # 第 2 行需要 13 针，但只有 12 针，缺 1 针
        data = self.post(12, 'k*12\nk*13\nk*12')
        self.assertFalse(data['ok'])
        self.assertEqual(len(data['rows']), 1)  # 错误行及之后不再推算
        err = data['error']
        self.assertEqual(err['type'], 'count')
        self.assertEqual(err['line'], 2)
        self.assertEqual(err['diff'], 1)

    def test_count_leftover(self):
        # 第 2 行只消耗 10 针，剩余 2 针未使用
        data = self.post(12, 'k*12\nk*10')
        self.assertFalse(data['ok'])
        err = data['error']
        self.assertEqual(err['line'], 2)
        self.assertEqual(err['diff'], -2)

    def test_parse_error_stops(self):
        data = self.post(4, 'k*4\nssk\nk*4')
        self.assertFalse(data['ok'])
        self.assertEqual(len(data['rows']), 1)
        self.assertEqual(data['error']['type'], 'parse')
        self.assertEqual(data['error']['line'], 2)

    def test_blank_lines_skipped_but_line_numbers_kept(self):
        data = self.post(2, 'k*2\n\nk*3')
        self.assertFalse(data['ok'])
        self.assertEqual(data['error']['line'], 3)

    def test_bad_cast_on(self):
        for bad in [0, -3, 'abc', '2.5', None, True]:
            data = self.post(bad, 'k*2')
            self.assertFalse(data['ok'], msg=repr(bad))
            self.assertEqual(data['error']['type'], 'cast_on')

    def test_string_cast_on_accepted(self):
        data = self.post('4', 'k*4')
        self.assertTrue(data['ok'])
        self.assertEqual(data['final'], 4)

    def test_single_size_rejects_multi_size_repeat(self):
        # 单尺码接口下 *{a|b} 项数（2）与尺码数（1）不符
        data = self.post(4, 'k*{2|2}')
        self.assertFalse(data['ok'])
        self.assertEqual(data['error']['type'], 'parse')
        self.assertIn('项数不符', data['error']['message'])


class TestValidateSizesApi(unittest.TestCase):
    SIZES = [{'name': 'S', 'cast_on': 12},
             {'name': 'M', 'cast_on': 16},
             {'name': 'L', 'cast_on': 20}]

    def setUp(self):
        self.client = app.test_client()

    def post(self, sizes, pattern, yarn=None):
        payload = {'sizes': sizes, 'pattern': pattern}
        if yarn is not None:
            payload['yarn'] = yarn
        return self.client.post('/api/validate-sizes', json=payload).get_json()

    def test_all_valid(self):
        pattern = '\n'.join([
            '(k2tog yo)*{6|8|10}',
            'p*{12|16|20}',
            '(k2tog)*{6|8|10}',
            '(k2tog)*{3|4|5}',
            'k*{3|4|5}',
        ])
        data = self.post(self.SIZES, pattern)
        self.assertTrue(data['ok'])
        self.assertEqual(len(data['sizes']), 3)
        self.assertEqual([s['name'] for s in data['sizes']], ['S', 'M', 'L'])
        self.assertEqual([s['final'] for s in data['sizes']], [3, 4, 5])
        for s in data['sizes']:
            self.assertTrue(s['ok'])
            self.assertIsNone(s['error'])
            self.assertEqual(len(s['rows']), 5)
        self.assertEqual(data['sizes'][0]['rows'][0]['in'], 12)
        self.assertEqual(data['sizes'][1]['rows'][0]['consume'], 16)
        self.assertEqual(data['sizes'][2]['rows'][2]['out'], 10)

    def test_plain_repeat_applies_to_every_size(self):
        sizes = [{'name': 'A', 'cast_on': 4}, {'name': 'B', 'cast_on': 4}]
        data = self.post(sizes, '(k2tog yo)*2\np*4')
        self.assertTrue(data['ok'])
        self.assertEqual([s['final'] for s in data['sizes']], [4, 4])

    def test_failing_size_stops_independently(self):
        # 第 2 行 M 差 1 针：仅 M 停止，S/L 继续推算到结束
        sizes = [{'name': 'S', 'cast_on': 4},
                 {'name': 'M', 'cast_on': 6},
                 {'name': 'L', 'cast_on': 8}]
        pattern = 'k*{4|6|8}\n(k yo)*{4|5|8}\nk*{8|1|16}'
        data = self.post(sizes, pattern)
        self.assertFalse(data['ok'])
        s, m, l = data['sizes']
        self.assertTrue(s['ok'])
        self.assertEqual(len(s['rows']), 3)
        self.assertEqual(s['final'], 8)
        self.assertFalse(m['ok'])
        self.assertEqual(m['error']['type'], 'count')
        self.assertEqual(m['error']['line'], 2)
        self.assertEqual(m['error']['diff'], -1)
        self.assertEqual(len(m['rows']), 1)  # 错误行及之后不再推算
        self.assertEqual(m['final'], 6)       # 最后有效行之后的针数
        self.assertTrue(l['ok'])
        self.assertEqual(len(l['rows']), 3)
        self.assertEqual(l['final'], 16)

    def test_parse_error_stops_all_sizes(self):
        data = self.post(self.SIZES, 'k*{12|16|20}\nssk\nk*1')
        self.assertFalse(data['ok'])
        for s in data['sizes']:
            self.assertFalse(s['ok'])
            self.assertEqual(s['error']['type'], 'parse')
            self.assertEqual(s['error']['line'], 2)
            self.assertEqual(len(s['rows']), 1)

    def test_size_count_mismatch_reports_line_and_char(self):
        data = self.post(self.SIZES, 'k*{12|16|20}\np*{6|8}')
        self.assertFalse(data['ok'])
        err = data['sizes'][0]['error']
        self.assertEqual(err['type'], 'parse')
        self.assertEqual(err['line'], 2)
        self.assertEqual(err['char'], 2)  # '*' 是第 2 个字符
        self.assertIn('项数不符', err['message'])

    def test_bad_multiplier_reports_line_and_char(self):
        data = self.post(self.SIZES, 'k*{12|16|20}\n(p)*0')
        err = data['sizes'][0]['error']
        self.assertEqual(err['type'], 'parse')
        self.assertEqual(err['line'], 2)
        self.assertEqual(err['char'], 5)  # '0' 是第 5 个字符

    def test_blank_lines_skipped_but_line_numbers_kept(self):
        data = self.post(self.SIZES, 'k*{12|16|20}\n\nssk')
        self.assertFalse(data['ok'])
        self.assertEqual(data['sizes'][0]['error']['line'], 3)

    def test_bad_sizes(self):
        for bad in [None, [], 'S', [{}], [{'name': '', 'cast_on': 4}],
                    [{'name': 'S'}], [{'name': 'S', 'cast_on': 0}],
                    [{'name': 'S', 'cast_on': True}],
                    [{'name': 'S', 'cast_on': 'x'}]]:
            data = self.post(bad, 'k*4')
            self.assertFalse(data['ok'], msg=repr(bad))
            self.assertEqual(data['error']['type'], 'sizes')
            self.assertEqual(data['sizes'], [])

    def test_string_cast_on_accepted(self):
        data = self.post([{'name': 'S', 'cast_on': '4'}], 'k*4')
        self.assertTrue(data['ok'])
        self.assertEqual(data['sizes'][0]['final'], 4)

    YARN = {'k': 0.5, 'p': 0.5, 'yo': 0.2, 'k2tog': 1.0,
            'waste_percent': 10, 'grams_per_ball': 50}

    def test_yarn_weights_per_row_cumulative(self):
        # S(12)：行1 k2tog*6+yo*6 = 6*1.0+6*0.2 = 7.2
        # 行2 p*12 = 6.0，累计 13.2
        # 行3 k2tog*6 = 6.0，累计 19.2
        pattern = '\n'.join([
            '(k2tog yo)*{6|8|10}',
            'p*{12|16|20}',
            '(k2tog)*{6|8|10}',
        ])
        data = self.post(self.SIZES, pattern, self.YARN)
        self.assertTrue(data['ok'])
        s = data['sizes'][0]
        self.assertEqual([r['weight'] for r in s['rows']], [7.2, 13.2, 19.2])
        yarn = s['yarn']
        self.assertFalse(yarn['partial'])
        self.assertTrue(yarn['complete'])
        self.assertEqual(yarn['raw_total'], 19.2)
        self.assertEqual(yarn['with_waste'], 21.12)
        self.assertEqual(yarn['balls'], 1)  # ceil(21.12/50) = 1

    def test_yarn_nested_and_size_repeats(self):
        # 记号次数按尺码分别累计：
        # S: k2tog=6 yo=6 p=12 k2tog=6 -> 6+1.2+6+6 = 19.2
        # M: k2tog=8 yo=8 p=16 k2tog=8 -> 8+1.6+8+8 = 25.6
        # L: k2tog=10 yo=10 p=20 k2tog=10 -> 10+2+10+10 = 32
        pattern = '\n'.join([
            '(k2tog yo)*{6|8|10}',
            'p*{12|16|20}',
            '(k2tog)*{6|8|10}',
        ])
        data = self.post(self.SIZES, pattern, self.YARN)
        raw = [s['yarn']['raw_total'] for s in data['sizes']]
        self.assertEqual(raw, [19.2, 25.6, 32])
        with_waste = [s['yarn']['with_waste'] for s in data['sizes']]
        self.assertEqual(with_waste, [21.12, 28.16, 35.2])
        # 每团 50g：S/M 各 1 团，L 也是 1 团（35.2 < 50）
        self.assertEqual([s['yarn']['balls'] for s in data['sizes']], [1, 1, 1])

    def test_yarn_balls_rounded_up(self):
        # S 含损耗 14.52g，每团 7g -> ceil(2.074) = 3 团
        pattern = '(k2tog yo)*{6|8|10}\np*{12|16|20}'
        yarn = dict(self.YARN, grams_per_ball=7)
        data = self.post(self.SIZES, pattern, yarn)
        s = data['sizes'][0]
        self.assertEqual(s['yarn']['raw_total'], 13.2)
        self.assertEqual(s['yarn']['with_waste'], 14.52)
        self.assertEqual(s['yarn']['balls'], 3)

    def test_yarn_exact_multiple_does_not_round_up_extra(self):
        # 一行 k2tog*10 原始 10g，损耗 0%，每团 5g -> 恰好 2 团而非 3 团
        sizes = [{'name': 'S', 'cast_on': 20}]
        yarn = {'k': 0, 'p': 0, 'yo': 0, 'k2tog': 1.0,
                'waste_percent': 0, 'grams_per_ball': 5}
        data = self.post(sizes, 'k2tog*10', yarn)
        self.assertTrue(data['ok'])
        self.assertEqual(data['sizes'][0]['yarn']['balls'], 2)

    def test_yarn_count_error_marks_partial_for_that_size(self):
        # 第 2 行 M 差 1 针：M 停止，用量仅含第 1 行且 partial；S/L 完整
        sizes = [{'name': 'S', 'cast_on': 4},
                 {'name': 'M', 'cast_on': 6},
                 {'name': 'L', 'cast_on': 8}]
        pattern = 'k*{4|6|8}\n(k yo)*{4|5|8}\nk*{8|1|16}'
        data = self.post(sizes, pattern, self.YARN)
        s, m, l = data['sizes']
        # S 完整：行1 k*4=2, 行2 k*4+yo*4=2.8 累计 4.8, 行3 k*8=4 累计 8.8
        self.assertFalse(s['yarn']['partial'])
        self.assertAlmostEqual(s['yarn']['raw_total'], 8.8)
        self.assertEqual(len(s['rows']), 3)
        # M 部分：仅行1 k*6 = 3.0
        self.assertTrue(m['yarn']['partial'])
        self.assertFalse(m['yarn']['complete'])
        self.assertEqual(m['yarn']['raw_total'], 3.0)
        self.assertEqual(len(m['rows']), 1)
        self.assertEqual(m['rows'][0]['weight'], 3.0)
        # L 完整
        self.assertFalse(l['yarn']['partial'])

    def test_yarn_parse_error_marks_all_partial(self):
        data = self.post(self.SIZES, 'k*{12|16|20}\nssk\nk*1', self.YARN)
        self.assertFalse(data['ok'])
        for s in data['sizes']:
            self.assertTrue(s['yarn']['partial'])
            # 行1：S k*12=6.0，M k*16=8.0，L k*20=10.0
        self.assertEqual([s['yarn']['raw_total'] for s in data['sizes']],
                         [6.0, 8.0, 10.0])

    def test_yarn_nested_group_count(self):
        # ((k yo)*2 k2tog)*3 单尺码：k=6, yo=6, k2tog=3
        # 原始 = 6*0.5 + 6*0.2 + 3*1.0 = 7.2
        # 消耗: (k yo)*2 消耗2产出4；k2tog 消耗2产出1 => 每组消耗4产出5
        # 整体 *3：消耗12产出15，故起针12
        sizes = [{'name': 'S', 'cast_on': 12}]
        data = self.post(sizes, '((k yo)*2 k2tog)*3', self.YARN)
        self.assertTrue(data['ok'])
        self.assertEqual(data['sizes'][0]['yarn']['raw_total'], 7.2)

    def test_yarn_invalid_params(self):
        pattern = 'k*4'
        good = {'k': 0.5, 'p': 0.5, 'yo': 0.2, 'k2tog': 1.0,
                'waste_percent': 10, 'grams_per_ball': 50}
        bad_payloads = [
            'not-an-object',
            {},  # 缺字段
            dict(good, k=-1),  # 负克重
            dict(good, k='abc'),
            dict(good, waste_percent=-5),
            dict(good, grams_per_ball=0),  # 每团必须大于 0
            dict(good, grams_per_ball='x'),
            dict(good, k=True),  # 布尔值不接受
        ]
        for yarn in bad_payloads:
            data = self.post([{'name': 'S', 'cast_on': 4}], pattern, yarn)
            self.assertFalse(data['ok'], msg=repr(yarn))
            self.assertEqual(data['error']['type'], 'yarn', msg=repr(yarn))
            self.assertEqual(data['sizes'], [])

    def test_yarn_numeric_strings_accepted(self):
        sizes = [{'name': 'S', 'cast_on': 2}]
        yarn = {'k': '0.5', 'p': '0.5', 'yo': '0.2', 'k2tog': '1',
                'waste_percent': '0', 'grams_per_ball': '10'}
        data = self.post(sizes, 'k2tog*1', yarn)
        self.assertTrue(data['ok'])
        self.assertEqual(data['sizes'][0]['yarn']['raw_total'], 1.0)

    def test_no_yarn_keeps_response_backward_compatible(self):
        data = self.post(self.SIZES, 'k*{12|16|20}')
        self.assertTrue(data['ok'])
        for s in data['sizes']:
            self.assertNotIn('yarn', s)
            self.assertNotIn('weight', s['rows'][0])


class TestValidateSizesSuggestion(unittest.TestCase):
    SIZES = [{'name': 'S', 'cast_on': 12},
             {'name': 'M', 'cast_on': 16},
             {'name': 'L', 'cast_on': 20}]

    def setUp(self):
        self.client = app.test_client()

    def post(self, sizes, pattern, yarn=None):
        payload = {'sizes': sizes, 'pattern': pattern}
        if yarn is not None:
            payload['yarn'] = yarn
        return self.client.post('/api/validate-sizes', json=payload).get_json()

    def test_count_error_yields_fix_suggestion(self):
        # 第 1 行 L 需要 *10 而非 *9；S/M 原本正确，不能受影响
        pattern = '(k2tog yo)*{6|8|9}\np*{12|16|20}'
        data = self.post(self.SIZES, pattern)
        self.assertFalse(data['ok'])
        sug = data['suggestion']
        self.assertIsNotNone(sug)
        self.assertEqual(sug['line'], 1)
        self.assertEqual(sug['char'], 11)  # '*' 是第 11 个字符
        self.assertEqual(sug['old'], '*{6|8|9}')
        self.assertEqual(sug['new'], '*{6|8|10}')
        self.assertEqual(sug['instruction'], '(k2tog yo)*{6|8|10}')
        self.assertEqual(sug['produces'], [12, 16, 20])
        # 原有结果字段不受影响
        self.assertEqual(data['sizes'][2]['error']['type'], 'count')
        self.assertEqual(data['sizes'][2]['error']['diff'], -2)
        self.assertEqual(len(data['sizes'][0]['rows']), 2)

    def test_applied_suggestion_passes_validation(self):
        pattern = '(k2tog yo)*{6|8|9}\np*{12|16|20}'
        data = self.post(self.SIZES, pattern)
        sug = data['suggestion']
        lines = pattern.split('\n')
        lines[sug['line'] - 1] = sug['instruction']
        fixed = self.post(self.SIZES, '\n'.join(lines))
        self.assertTrue(fixed['ok'])
        self.assertIsNone(fixed['suggestion'])
        self.assertEqual([s['final'] for s in fixed['sizes']], [12, 16, 20])

    def test_no_error_gives_null_suggestion(self):
        data = self.post(self.SIZES, '(k2tog yo)*{6|8|10}\np*{12|16|20}')
        self.assertTrue(data['ok'])
        self.assertIsNone(data['suggestion'])

    def test_unsolvable_row_gives_null_suggestion(self):
        # (k2tog)*n 消耗总是偶数，7 针无法通过改倍数修正
        sizes = [{'name': 'S', 'cast_on': 7}]
        data = self.post(sizes, '(k2tog)*3')
        self.assertFalse(data['ok'])
        self.assertIsNone(data['suggestion'])
        self.assertEqual(data['sizes'][0]['error']['type'], 'count')

    def test_parse_error_gives_null_suggestion(self):
        data = self.post(self.SIZES, 'k*{12|16|20}\nssk')
        self.assertFalse(data['ok'])
        self.assertIsNone(data['suggestion'])

    def test_suggestion_targets_first_error_row(self):
        # 第 2 行 M 差额（首个错误行）；建议只改第 2 行的倍数
        sizes = [{'name': 'S', 'cast_on': 4},
                 {'name': 'M', 'cast_on': 6},
                 {'name': 'L', 'cast_on': 8}]
        pattern = 'k*{4|6|8}\n(k yo)*{4|5|8}\nk*{8|1|16}'
        data = self.post(sizes, pattern)
        sug = data['suggestion']
        self.assertIsNotNone(sug)
        self.assertEqual(sug['line'], 2)
        self.assertEqual(sug['instruction'], '(k yo)*{4|6|8}')
        self.assertEqual(sug['produces'], [8, 12, 16])
        # 应用后 M 推进到第 3 行才出错，并给出新的建议
        lines = pattern.split('\n')
        lines[sug['line'] - 1] = sug['instruction']
        fixed = self.post(sizes, '\n'.join(lines))
        s, m, l = fixed['sizes']
        self.assertTrue(s['ok'])
        self.assertTrue(l['ok'])
        self.assertFalse(m['ok'])
        self.assertEqual(m['error']['line'], 3)
        self.assertEqual(fixed['suggestion']['line'], 3)
        self.assertEqual(fixed['suggestion']['instruction'], 'k*{8|12|16}')

    def test_suggestion_with_yarn_request(self):
        # 线材用量估算与建议互不影响
        yarn = {'k': 0.5, 'p': 0.5, 'yo': 0.2, 'k2tog': 1.0,
                'waste_percent': 10, 'grams_per_ball': 50}
        data = self.post(self.SIZES, '(k2tog yo)*{6|8|9}\np*{12|16|20}', yarn)
        self.assertFalse(data['ok'])
        self.assertEqual(data['suggestion']['instruction'], '(k2tog yo)*{6|8|10}')
        self.assertTrue(data['sizes'][2]['yarn']['partial'])
        self.assertFalse(data['sizes'][0]['yarn']['partial'])

    def test_single_size_api_has_no_suggestion(self):
        data = self.client.post('/api/validate', json={
            'cast_on': 7, 'pattern': '(k2tog)*3',
        }).get_json()
        self.assertFalse(data['ok'])
        self.assertNotIn('suggestion', data)


class TestMoveSizeApi(unittest.TestCase):
    SIZES = [{'name': 'S', 'cast_on': 12},
             {'name': 'M', 'cast_on': 16},
             {'name': 'L', 'cast_on': 20}]
    PATTERN = '\n'.join([
        '(k2tog yo)*{6|8|10}',
        'p*{12|16|20}',
        '(k2tog)*{6|8|10}',
        '(k2tog)*{3|4|5}',
        'k*{3|4|5}',
    ])

    def setUp(self):
        self.client = app.test_client()

    def move(self, sizes, pattern, index, direction):
        return self.client.post('/api/move-size', json={
            'sizes': sizes, 'pattern': pattern,
            'index': index, 'direction': direction,
        }).get_json()

    def validate(self, sizes, pattern):
        return self.client.post('/api/validate-sizes', json={
            'sizes': sizes, 'pattern': pattern,
        }).get_json()

    def _results_by_name(self, data):
        return {s['name']: {
            'ok': s['ok'],
            'final': s['final'],
            'error': None if s['error'] is None else (
                s['error']['type'], s['error'].get('line'), s['error'].get('diff')),
            'rows': [(r['line'], r['consume'], r['produce'], r['out']) for r in s['rows']],
        } for s in data['sizes']}

    def test_move_down_swaps_sizes_and_pattern(self):
        # M 下移：[S,M,L] -> [S,L,M]
        data = self.move(self.SIZES, self.PATTERN, 1, 'down')
        self.assertTrue(data['ok'])
        self.assertEqual([s['name'] for s in data['sizes']], ['S', 'L', 'M'])
        self.assertEqual([s['cast_on'] for s in data['sizes']], [12, 20, 16])
        self.assertEqual(data['pattern'].splitlines(), [
            '(k2tog yo)*{6|10|8}',
            'p*{12|20|16}',
            '(k2tog)*{6|10|8}',
            '(k2tog)*{3|5|4}',
            'k*{3|5|4}',
        ])

    def test_move_up_swaps_sizes_and_pattern(self):
        # L 上移：[S,M,L] -> [S,L,M]
        data = self.move(self.SIZES, self.PATTERN, 2, 'up')
        self.assertTrue(data['ok'])
        self.assertEqual([s['name'] for s in data['sizes']], ['S', 'L', 'M'])
        self.assertEqual(data['pattern'].splitlines()[0], '(k2tog yo)*{6|10|8}')

    def test_plain_repeats_and_text_unchanged(self):
        pattern = '((k yo)*2 k2tog)*3\nk*{3|4|5} tail k*9\n'
        data = self.move(self.SIZES, pattern, 0, 'down')
        self.assertTrue(data['ok'])
        self.assertEqual(data['pattern'].splitlines()[0], '((k yo)*2 k2tog)*3')
        self.assertEqual(data['pattern'].splitlines()[1], 'k*{4|3|5} tail k*9')
        self.assertTrue(data['pattern'].endswith('\n'))

    def test_nested_and_multiple_sites_per_line(self):
        pattern = '(k*{2|3|4} yo)*2 p*{5|6|7}'
        data = self.move(self.SIZES, pattern, 0, 'down')
        self.assertTrue(data['ok'])
        self.assertEqual(data['pattern'], '(k*{3|2|4} yo)*2 p*{6|5|7}')

    def test_crlf_and_blank_lines_preserved(self):
        pattern = 'k*{1|2|3}\r\n\r\np*{4|5|6}'
        data = self.move(self.SIZES, pattern, 2, 'up')
        self.assertTrue(data['ok'])
        self.assertEqual(data['pattern'], 'k*{1|3|2}\r\n\r\np*{4|6|5}')

    def test_validation_results_identical_after_move(self):
        # 重排后各尺码重新校验的针数与移动前一致（按尺码名对照）
        before = self._results_by_name(self.validate(self.SIZES, self.PATTERN))
        moved = self.move(self.SIZES, self.PATTERN, 0, 'down')
        after = self._results_by_name(
            self.validate(moved['sizes'], moved['pattern']))
        self.assertEqual(before, after)

    def test_results_identical_with_count_errors(self):
        # 移动前 M 在第 2 行针数出错；移动后按名字对照结果仍一致
        sizes = [{'name': 'S', 'cast_on': 4},
                 {'name': 'M', 'cast_on': 6},
                 {'name': 'L', 'cast_on': 8}]
        pattern = 'k*{4|6|8}\n(k yo)*{4|5|8}\nk*{8|1|16}'
        before = self._results_by_name(self.validate(sizes, pattern))
        moved = self.move(sizes, pattern, 0, 'down')  # -> [M,S,L]
        after = self._results_by_name(
            self.validate(moved['sizes'], moved['pattern']))
        self.assertEqual(before, after)
        self.assertEqual(moved['pattern'], 'k*{6|4|8}\n(k yo)*{5|4|8}\nk*{1|8|16}')

    def test_count_mismatch_blocks_whole_move(self):
        # 第 2 行倍数只有 2 项：整次移动不生效（无 sizes/pattern 回写）
        pattern = 'k*{6|8|10}\np*{12|16}\n'
        data = self.move(self.SIZES, pattern, 0, 'down')
        self.assertFalse(data['ok'])
        self.assertNotIn('sizes', data)
        self.assertNotIn('pattern', data)
        err = data['error']
        self.assertEqual(err['type'], 'reorder')
        self.assertEqual(err['line'], 2)
        self.assertEqual(err['char'], 2)
        self.assertIn('项数不符', err['message'])

    def test_bad_value_blocks_whole_move(self):
        pattern = 'k*{6|0|10}'
        data = self.move(self.SIZES, pattern, 2, 'up')
        self.assertFalse(data['ok'])
        err = data['error']
        self.assertEqual(err['type'], 'reorder')
        self.assertEqual(err['line'], 1)
        self.assertEqual(err['char'], 6)  # '0' 是第 6 个字符
        self.assertIn('正整数', err['message'])

    def test_invalid_sizes_rejected(self):
        data = self.move([], 'k*1', 0, 'up')
        self.assertFalse(data['ok'])
        self.assertEqual(data['error']['type'], 'sizes')

    def test_boundary_and_bad_direction_rejected(self):
        for index, direction in [(0, 'up'), (2, 'down')]:
            data = self.move(self.SIZES, self.PATTERN, index, direction)
            self.assertFalse(data['ok'], msg=(index, direction))
            self.assertEqual(data['error']['type'], 'move')
        data = self.move(self.SIZES, self.PATTERN, 1, 'sideways')
        self.assertFalse(data['ok'])
        self.assertEqual(data['error']['type'], 'move')
        data = self.move(self.SIZES, self.PATTERN, 9, 'up')
        self.assertFalse(data['ok'])
        self.assertEqual(data['error']['type'], 'move')

    def test_single_size_move_is_boundary_error(self):
        sizes = [{'name': 'S', 'cast_on': 4}]
        data = self.move(sizes, 'k*{4}', 0, 'up')
        self.assertFalse(data['ok'])
        self.assertEqual(data['error']['type'], 'move')


if __name__ == '__main__':
    unittest.main()
