import base64
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser_engine import CompatibleParser, decode_bytes, parse_text, read_text


class ParserTests(unittest.TestCase):
    def assert_parses(self, source, expected, **options):
        result = parse_text(source, **options)
        self.assertEqual(expected, result.value, source)
        self.assertEqual(expected, json.loads(result.render()))
        json.loads(result.render(compact=True, sort_keys=True))
        return result

    def test_standard_types_and_precision(self):
        for value in (None, True, False, 0, -3, 1.25, "你好", [], {},
                      {"id": 999999999999999999999999999999, "text": "&quot;中文\\路径", "nested": [1, False, None]}):
            with self.subTest(value=value):
                self.assert_parses(json.dumps(value, ensure_ascii=False), value)

    def test_customer_reference_documents(self):
        source, encoding = read_text(Path(__file__).resolve().parents[1] / "参考数据.txt")
        result = parse_text(source)
        self.assertEqual(2, len(result.value))
        self.assertEqual([5, 15], [len(item["data"]["records"]) for item in result.value])
        self.assertEqual("开始", result.value[0]["data"]["records"][0]["nodeName"])
        self.assertEqual(15, result.value[1]["data"]["total"])
        self.assertTrue(result.value[0]["ok"])
        self.assertIsNone(result.value[0]["msg"])
        self.assertEqual("utf-8", encoding)

    def test_wrappers_and_encodings(self):
        expected = {"name": "中文", "ok": True}
        raw = json.dumps(expected, ensure_ascii=False)
        cases = [raw, "\ufeff" + raw, json.dumps(raw), json.dumps(json.dumps(raw)),
                 "'" + raw + "'", "```json\n" + raw + "\n```", "const response = " + raw + ";",
                 "window.result = " + raw + ";", "callback(" + raw + ");",
                 quote(raw), base64.b64encode(raw.encode()).decode(),
                 "data:application/json;base64," + base64.b64encode(raw.encode()).decode(),
                 raw.replace('"', "&quot;"), raw.replace('"', "&amp;quot;"),
                 raw.replace('"', '\\"'), "<Response><![CDATA[" + raw + "]]></Response>",
                 '<Response>' + raw.replace('"', '&quot;') + '</Response>']
        for source in cases:
            with self.subTest(source=source):
                self.assert_parses(source, expected)

    def test_json5_and_python(self):
        cases = [("{name:'你好', ok:true, list:[1,2,], /* comment */}", {"name": "你好", "ok": True, "list": [1, 2]}),
                 ("{'ok': True, 'none': None, 'items': (1, 2)}", {"ok": True, "none": None, "items": [1, 2]}),
                 ("{hex:0x10, fraction:.5, positive:+2}", {"hex": 16, "fraction": .5, "positive": 2}),
                 ("{1: 'one'}", {"1": "one"})]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assert_parses(source, expected)

    def test_damaged_response_preserves_outer_fields(self):
        source = 'Respon1&quot;data&quot;:{&quot;records&quot;:[{&quot;flowId&quot;:2438,&quot;nodeName&quot;:&quot;开始&quot;}],&quot;total&quot;:15},&quot;ok&quot;:true}\\</Response>'
        expected = {"data": {"records": [{"flowId": 2438, "nodeName": "开始"}], "total": 15}, "ok": True}
        result = self.assert_parses(source, expected)
        self.assertTrue(result.warnings)
        self.assertNotIn("code", result.value)
        with self.assertRaises(ValueError):
            parse_text(source, strict=True)

    def test_damaged_customer_response_all_records(self):
        source, encoding = read_text(Path(__file__).resolve().parents[1] / "参考数据.txt")
        first = source.split("</Response>", 1)[0]
        damaged = "Respon1" + first[first.index("&quot;data&quot;"):] + "\\</Response>"
        expected = parse_text(source).value[0]
        expected.pop("code")
        expected.pop("msg")
        result = self.assert_parses(damaged, expected)
        self.assertEqual(5, len(result.value["data"]["records"]))

    def test_damaged_response_wrapper_and_entity_layers(self):
        expected = {"data": {"records": [{"id": 1}, {"id": 2}]}, "ok": True}
        fragment = json.dumps(expected)[1:]
        for prefix in ("Respon1", "<Response>", ""):
            for suffix in ("</Response>", "\\</Response>"):
                for encoding in (lambda value: value, lambda value: value.replace('"', "&quot;"),
                                 lambda value: value.replace('"', "&amp;quot;")):
                    with self.subTest(prefix=prefix, suffix=suffix):
                        result = self.assert_parses(prefix + encoding(fragment) + suffix, expected)
                        self.assertTrue(result.warnings)

    def test_repair(self):
        for source, expected in [("{\"a\": 1", {"a": 1}), ('{"a":1 "b":2}', {"a": 1, "b": 2}),
                                 ('[1,2,3', [1, 2, 3]), ('{name: test}', {"name": "test"})]:
            with self.subTest(source=source):
                result = self.assert_parses(source, expected)
                self.assertTrue(result.warnings)

    def test_logs_and_multiple_documents(self):
        for source in ('{"a":1}\n{"b":2}', '[INFO] response {"a":1}\nnext {"b":2}', '{"a":1}{"b":2}'):
            self.assert_parses(source, [{"a": 1}, {"b": 2}])
        result = self.assert_parses('HTTP/1.1 200 OK\nContent-Type: application/json\n\n{"data":[1]}', {"data": [1]})
        self.assertTrue(result.warnings)

    def test_xml(self):
        self.assert_parses('<root id="7"><item>A</item><item>B</item><empty/></root>',
                           {"root": {"@id": "7", "item": ["A", "B"], "empty": ""}})
        self.assert_parses('<root><data>{"a":1}</data></root>', {"root": {"data": {"a": 1}}})
        self.assert_parses('<r>&lt;hello&gt; &amp; bye</r>', {"r": "<hello> & bye"})
        result = parse_text('<p>Hello <b>bold</b> world</p>')
        self.assertEqual("Hello world", result.value["p"]["#text"])
        self.assertTrue(result.warnings)

    def test_yaml(self):
        self.assert_parses("name: 中文\nitems:\n  - 1\n  - 2\nactive: true\n", {"name": "中文", "items": [1, 2], "active": True})
        self.assert_parses("date: 2025-01-02\n", {"date": "2025-01-02"})
        self.assert_parses("---\na: 1\n---\nb: 2", [{"a": 1}, {"b": 2}])

    def test_csv(self):
        self.assert_parses('name,code\n中文,001\n"a,b",002', [{"name": "中文", "code": "001"}, {"name": "a,b", "code": "002"}])
        self.assert_parses("name\tage\n甲\t20\n乙\t21", [{"name": "甲", "age": "20"}, {"name": "乙", "age": "21"}])
        self.assert_parses("a,a\n1,2", [["a", "a"], ["1", "2"]])

    def test_queries(self):
        self.assert_parses("name=%E4%B8%AD%E6%96%87&a=1&a=2&empty=", {"name": "中文", "a": ["1", "2"], "empty": ""})
        self.assert_parses('data={"x":1}&id=2', {"data": '{"x":1}', "id": "2"})

    def test_plain_text_is_preserved(self):
        for source in ("随便一段文字", "  保留空白\n\t", "hello world", "import os; os.system('echo bad')"):
            result = self.assert_parses(source, source)
            self.assertTrue(result.warnings)

    def test_nested_is_opt_in(self):
        source = '{"data":"{\\\"id\\\":1}","code":"001"}'
        self.assert_parses(source, {"data": '{"id":1}', "code": "001"})
        self.assert_parses(source, {"data": {"id": 1}, "code": "001"}, decode_nested=True)

    def test_strict_mode(self):
        self.assert_parses('"{\\\"a\\\":1}"', '{"a":1}', strict=True)
        for source in ("{a:1}", "<Response>{}</Response>", '{"a":1,}', "plain", "NaN", "Infinity", "1e999"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_text(source, strict=True)

    def test_nonfinite_and_duplicates(self):
        result = self.assert_parses('{"a":NaN,"b":Infinity,"c":-Infinity}', {"a": None, "b": None, "c": None})
        self.assertTrue(result.warnings)
        result = self.assert_parses('{"a":1,"a":2}', {"a": 2})
        self.assertTrue(any("重复键" in warning for warning in result.warnings))

    def test_reuse_resets_notes(self):
        parser = CompatibleParser()
        parser.parse('{"a":NaN}')
        self.assertEqual([], parser.parse('{"a":1}').warnings)

    def test_encodings(self):
        text = '{"name":"中文"}'
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "gb18030"):
            with self.subTest(encoding=encoding):
                self.assertEqual(text, decode_bytes(text.encode(encoding))[0])
        with self.assertRaises(ValueError):
            decode_bytes(b"\x00\x01\x00\x02\x03\x00")

    def test_does_not_execute_xml_entities(self):
        source = '<!DOCTYPE x [<!ENTITY a SYSTEM "file:///C:/Windows/win.ini">]><x>&a;</x>'
        result = self.assert_parses(source, source)
        self.assertTrue(result.warnings)

    def test_empty_and_deep_inputs(self):
        for source in ("", "\n\t "):
            with self.assertRaises(ValueError):
                parse_text(source)
        # 深层但合法的 JSON 应当照常解析（标准 JSON 走快路径，不再逐节点规整）
        self.assertEqual("JSON", parse_text("[" * 200 + "0" + "]" * 200).format)
        # 深到解释器栈都撑不住时才报错
        with self.assertRaises(ValueError):
            parse_text("[" * 20000 + "0" + "]" * 20000)
        with self.assertRaises(ValueError):
            parse_text("a: &a [*a]")

    def test_standard_json_is_not_capped_by_node_count(self):
        """标准 JSON 走快路径，不再受「节点数上限」约束（大文件才进得来）。"""
        source = json.dumps([{"id": index, "name": f"项目{index}"} for index in range(400)])
        with patch("parser_engine.MAX_NODES", 10):
            self.assertEqual("JSON", parse_text(source).format)
            # 需要逐节点规整的格式仍然受上限保护
            with self.assertRaises(ValueError):
                parse_text("\n".join(f"key{index}: {index}" for index in range(30)))


if __name__ == "__main__":
    unittest.main()
