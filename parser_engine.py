from __future__ import annotations

import ast
import base64
import csv
import datetime
import html
import io
import json
import math
import re
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from urllib.parse import parse_qsl, unquote


# 重量级第三方依赖（json5 / yaml / defusedxml / json_repair / charset-normalizer）
# 全部改为首次使用时才导入，否则启动时会白白付出几百毫秒的导入开销。
# 因为是动态导入，PyInstaller 扫不到，打包时必须作为 hidden-import 显式带上：
# 见 build.py 的 HIDDEN_IMPORTS，tests/test_release.py 会校验两边一致。
LAZY_MODULES = ("json5", "yaml", "defusedxml.ElementTree", "json_repair", "charset_normalizer")

_IMPORT_CACHE: dict[str, object] = {}


def require(name: str):
    """按需导入并返回模块；结果缓存，重复调用零开销。"""
    module = _IMPORT_CACHE.get(name)
    if module is None:
        module = import_module(name)
        _IMPORT_CACHE[name] = module
    return module


# 单次可解析的字符上限（约 512 MB UTF-8）。此前的 20 MB 硬限制已取消。
MAX_FILE_CHARS = 512 * 1024 * 1024
MAX_LAYERS = 12
# 只有 JSON5 / YAML / XML / CSV 这类需要逐节点规整的格式才受节点数约束；
# 标准 JSON 走快路径，不受此限制。
MAX_NODES = 20_000_000


@dataclass
class ParseResult:
    value: object
    format: str
    steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def render(self, compact: bool = False, sort_keys: bool = False) -> str:
        return json.dumps(
            self.value, ensure_ascii=False, allow_nan=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None, sort_keys=sort_keys,
        )


def decode_bytes(data: bytes) -> tuple[str, str]:
    if len(data) > MAX_FILE_CHARS * 4:
        raise ValueError(f"文件超过 {MAX_FILE_CHARS // 1024 // 1024} MB，请先拆分后导入。")
    for marker, encoding in ((b"\xff\xfe\x00\x00", "utf-32"),
                             (b"\x00\x00\xfe\xff", "utf-32"),
                             (b"\xef\xbb\xbf", "utf-8-sig"),
                             (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(marker):
            return data.decode(encoding), encoding
    if b"\x00" in data[:200]:
        for encoding in ("utf-16-le", "utf-16-be"):
            try:
                decoded = data.decode(encoding)
                if decoded.lstrip().startswith(("{", "[", "<", '"')):
                    return decoded, encoding
            except UnicodeError:
                pass
        raise ValueError("文件包含二进制内容，请导入文本文件。")
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeError:
            pass
    match = from_bytes(data).best()
    if match is None:
        raise ValueError("无法识别文本编码，请将文件另存为 UTF-8。")
    return str(match), match.encoding


def read_text(path: str | Path) -> tuple[str, str]:
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError:
        size = 0
    if size > MAX_FILE_CHARS * 4:
        raise ValueError(f"文件超过 {MAX_FILE_CHARS // 1024 // 1024} MB，请先拆分后导入。")
    return decode_bytes(source.read_bytes())


class CompatibleParser:
    def __init__(self, strict: bool = False, decode_nested: bool = False):
        self.strict = strict
        self.decode_nested = decode_nested
        self.steps: list[str] = []
        self.warnings: list[str] = []
        self.node_count = 0

    def parse(self, source: str) -> ParseResult:
        self.steps = []
        self.warnings = []
        self.node_count = 0
        if len(source) > MAX_FILE_CHARS:
            raise ValueError(f"输入超过 {MAX_FILE_CHARS // 1024 // 1024} MB，请分段解析。")
        text = source.lstrip("\ufeff").strip()
        if not text:
            raise ValueError("请先粘贴文本或打开文件。")
        try:
            value, detected = self._parse(text, 0)
            if detected in ("纯文本", "文本"):
                value = source
            if self.decode_nested:
                value = self._nested(value, 0)
            # 标准 JSON 由 json.loads 直接产出，键必为字符串、非有限数值已在
            # parse_constant 中转成 null，无需再整棵树重建一遍——大文件靠这条
            # 快路径才不会卡住。其余格式（JSON5 / YAML / XML / CSV）仍需规整。
            if detected != "JSON":
                value = self._normalize(value, 0)
            result = ParseResult(value, detected, self.steps[:], list(dict.fromkeys(self.warnings)))
            result.render()
            return result
        except (RecursionError, MemoryError) as error:
            raise ValueError("内容层级过深或过于复杂，请拆分后解析。") from error

    def _pairs(self, pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                self.warnings.append(f"重复键 {key!r}：保留最后一个值。")
            result[key] = value
        return result

    def _constant(self, value):
        if self.strict:
            raise ValueError(f"标准 JSON 不支持 {value}。")
        self.warnings.append(f"{value} 不是标准 JSON 数值，已转换为 null。")
        return None

    def _loads(self, text):
        return json.loads(text, object_pairs_hook=self._pairs, parse_constant=self._constant)

    def _parse(self, text: str, depth: int) -> tuple[object, str]:
        if depth >= MAX_LAYERS:
            self.warnings.append("达到自动解包上限，剩余内容保留为字符串。")
            return text, "文本"
        try:
            value = self._loads(text)
            self.steps.append("解析标准 JSON")
            if not self.strict and isinstance(value, str) and self._looks_structured(value):
                self.steps.append("解开 JSON 字符串外层转义")
                return self._parse(value.strip(), depth + 1)
            return value, "JSON"
        except (ValueError, TypeError):
            if self.strict:
                raise ValueError("输入不是有效的标准 JSON；可关闭「严格模式」尝试兼容解析。")

        if text.startswith(("{", "[", "'", '"', "(")) or text in ("True", "False", "None"):
            try:
                value = ast.literal_eval(text)
                self.steps.append("转换 Python 字面量（单引号 / True / False / None / 元组）")
                if isinstance(value, str) and self._looks_structured(value):
                    return self._parse(value.strip(), depth + 1)
                return value, "Python 字面量"
            except (ValueError, SyntaxError, TypeError):
                pass
            json5 = require("json5")
            try:
                value = json5.loads(text, object_pairs_hook=self._pairs, parse_constant=self._constant)
                self.steps.append("解析 JSON5（注释 / 单引号 / 未加引号的键 / 尾逗号）")
                return value, "JSON5"
            except (ValueError, TypeError):
                pass

        fence = re.fullmatch(r"```[^\n]*\n([\s\S]*?)\n?```", text)
        if fence:
            self.steps.append("移除 Markdown 代码围栏")
            return self._parse(fence.group(1).strip(), depth + 1)
        if "&" in text and re.fullmatch(r"[^\s=&]+=[^\n]*(?:&[^\s=&]+=[^\n]*)+", text):
            return self._query(text)
        wrapper = re.fullmatch(
            r"(?:(?:var|let|const)\s+)?[\w.$]+\s*=\s*([\s\S]+?);?", text)
        callback = re.fullmatch(r"[\w.$]+\s*\(([\s\S]*)\)\s*;?", text)
        if wrapper or callback:
            inner = (wrapper or callback).group(1).strip().rstrip(";")
            if self._looks_structured(inner):
                self.steps.append("移除变量赋值 / JSONP 外壳")
                return self._parse(inner, depth + 1)

        recovered = self._recover_response(text)
        if recovered is not None:
            return recovered
        if text.startswith("<"):
            if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
                self.warnings.append("XML 包含 DTD / 实体声明，已保留原文，未展开实体。")
                return text, "文本"
            ElementTree = require("defusedxml.ElementTree")
            try:
                try:
                    root = ElementTree.fromstring(text)
                    roots = [root]
                except Exception:
                    root = ElementTree.fromstring("<__documents__>" + text + "</__documents__>")
                    roots = list(root)
                self.steps.append(f"解析 XML 外壳（{len(roots)} 段）并解码实体")
                values = [self._xml_document(element, depth + 1) for element in roots]
                return (values[0] if len(values) == 1 else values), "XML / 内嵌 JSON"
            except Exception:
                pass

        unescaped = html.unescape(text)
        if unescaped != text and self._looks_structured(unescaped):
            self.steps.append("解码 HTML 实体（如 &quot;、&amp;）")
            return self._parse(unescaped.strip(), depth + 1)
        if text.startswith((r'{\"', r'[\"', r'\"')):
            try:
                decoded = json.loads('"' + text + '"')
                self.steps.append("解开缺少外层引号的转义文本")
                return self._parse(decoded.strip(), depth + 1)
            except ValueError:
                pass
        decoded_url = unquote(text)
        if decoded_url != text and self._looks_structured(decoded_url):
            self.steps.append("解码 URL 百分号编码")
            return self._parse(decoded_url.strip(), depth + 1)
        encoded = text.split(",", 1)[1] if re.match(r"data:[^,]*;base64,", text) else text
        if len(encoded) >= 8 and re.fullmatch(r"[A-Za-z0-9+/_=\-\s]+", encoded):
            try:
                payload = base64.b64decode(encoded.strip() + "=" * (-len(encoded.strip()) % 4), altchars=b"-_")
                decoded = payload.decode("utf-8-sig")
                if self._looks_structured(decoded):
                    self.steps.append("解码 Base64 文本")
                    return self._parse(decoded.strip(), depth + 1)
            except (ValueError, UnicodeError):
                pass

        documents = self._extract_documents(text)
        if documents and (len(documents) > 1 or not text.startswith(("{", "["))):
            self.steps.append(f"从连续 JSON / 日志中提取 {len(documents)} 段 JSON")
            self.warnings.append("已提取 JSON 数据；外部日志、标记或分隔符未包含在结果中。")
            return (documents[0] if len(documents) == 1 else documents), "连续 JSON / 日志"

        if text.startswith(("{", "[")):
            repair_json = require("json_repair").repair_json
            try:
                repaired, repair_log = repair_json(text, return_objects=True, logging=True, skip_json_loads=True)
                if isinstance(repaired, (dict, list)):
                    self.steps.append("修复不完整 / 不规范的 JSON")
                    self.warnings.append("已启发式修复，可能补齐引号、分隔符、值或括号，请核对结果。")
                    for entry in repair_log[:6]:
                        self.warnings.append("修复细节：" + str(entry.get("text", entry)))
                    return repaired, "修复 JSON"
            except (ValueError, TypeError, IndexError):
                pass

        if re.search(r"(?m)^\s*(?:[^\s:#][^\n]*:\s|[-]\s+\S)", text):
            yaml = require("yaml")
            try:
                values = list(yaml.safe_load_all(text))
                if values and all(isinstance(value, (dict, list)) for value in values):
                    self.steps.append("使用安全 YAML 加载器转换为 JSON")
                    self.warnings.append("YAML 会按其规则识别数字、日期和布尔值，请核对类型。")
                    return (values[0] if len(values) == 1 else values), "YAML"
            except (yaml.YAMLError, ValueError, TypeError):
                pass
        if "\n" in text:
            try:
                dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
                rows = list(csv.reader(io.StringIO(text), dialect))
                if len(rows) > 1 and len(rows[0]) > 1 and all(len(row) == len(rows[0]) for row in rows):
                    headers = rows[0]
                    if len(set(headers)) == len(headers) and all(headers):
                        self.steps.append("转换表格：首行为字段名，单元格保留字符串")
                        return [dict(zip(headers, row)) for row in rows[1:]], "CSV / TSV"
                    self.steps.append("表头为空或重复，按二维数组保留所有单元格")
                    return rows, "CSV / TSV"
            except (csv.Error, ValueError):
                pass
        if re.fullmatch(r"[^\s=&]+=[^\n]*(?:&[^\s=&]+=[^\n]*)*", text):
            return self._query(text)
        if documents:
            self.warnings.append("JSON 后的额外文本未包含在结果中。")
            return documents[0], "JSON 提取"
        self.steps.append("将原文编码为合法 JSON 字符串")
        self.warnings.append("未识别到可靠的结构，原文作为字符串保留；未推测字段。")
        return text, "纯文本"

    def _recover_response(self, text):
        ending = re.search(r"(?:\\+\s*)?</\s*Response\s*>\s*$", text, re.I)
        if not ending:
            return None
        body = text[:ending.start()].strip()
        for unused in range(MAX_LAYERS):
            decoded = html.unescape(body)
            if decoded == body:
                break
            body = decoded
        start = re.search(r'[\[{]|"(?:\\.|[^"\\])*"\s*:', body)
        if not start:
            return None
        prefix = body[:start.start()].strip()
        if not re.fullmatch(r"(?:<\s*Response\s*>|Resp[\w ]*)?", prefix, re.I):
            return None
        fragment = body[start.start():]
        if fragment.startswith('"'):
            fragment = "{" + fragment
        elif re.fullmatch(r"<\s*Response\s*>", prefix, re.I) and not ending.group().startswith("\\"):
            return None
        try:
            value = self._loads(fragment)
        except (ValueError, TypeError):
            return None
        if not isinstance(value, (dict, list)):
            return None
        self.steps.append("解码 HTML 实体，移除损坏的 Response 外壳并恢复 JSON 对象边界")
        self.warnings.append("Response 外壳或 JSON 起始边界损坏，已按现存字段恢复；缺失的字段未补造，请核对结果。")
        return value, "Response 片段恢复"

    def _looks_structured(self, text: str) -> bool:
        for unused in range(MAX_LAYERS):
            text = text.lstrip()
            if text.startswith(("{", "[", "<", "```", r'{\"', r'[\"')):
                return True
            if not text.startswith('"'):
                return False
            try:
                value = json.loads(text)
            except ValueError:
                return False
            if not isinstance(value, str) or value == text:
                return False
            text = value
        return False

    def _query(self, text):
        result = {}
        for key, value in parse_qsl(text, keep_blank_values=True):
            if key in result:
                if not isinstance(result[key], list):
                    result[key] = [result[key]]
                result[key].append(value)
            else:
                result[key] = value
        self.steps.append("转换 URL 查询参数；重复参数保留为数组")
        return result, "查询参数"

    def _xml_document(self, element, depth: int):
        content = (element.text or "").strip()
        if not list(element) and not element.attrib and self._looks_structured(content):
            parsed, detected = self._parse(content, depth)
            if detected not in ("纯文本", "文本"):
                return parsed
        return {element.tag: self._xml_value(element, depth)}

    def _xml_value(self, element, depth: int):
        if depth > 100:
            raise ValueError("XML 层级超过 100 层。")
        children = list(element)
        content = (element.text or "").strip()
        if not children and not element.attrib:
            return self._parse(content, depth)[0] if self._looks_structured(content) else content
        result = {"@" + key: value for key, value in element.attrib.items()}
        grouped = {}
        for child in children:
            grouped.setdefault(child.tag, []).append(self._xml_value(child, depth + 1))
        result.update({key: values[0] if len(values) == 1 else values for key, values in grouped.items()})
        tail_text = [child.tail.strip() for child in children if child.tail and child.tail.strip()]
        if content or tail_text:
            result["#text"] = " ".join(([content] if content else []) + tail_text)
        if tail_text:
            self.warnings.append("XML 混合文本合并到 #text；同名子节点按数组保存，节点交错顺序不保留。")
        return result

    def _extract_documents(self, text):
        decoder = json.JSONDecoder(object_pairs_hook=self._pairs, parse_constant=self._constant)
        documents = []
        offset = 0
        attempts = 0
        opener = re.compile(r"[\[{]")
        while offset < len(text):
            match = opener.search(text, offset)
            if not match:
                break
            start = match.start()
            attempts += 1
            if attempts > 2000:
                self.warnings.append("达到日志提取扫描上限，建议分段处理。")
                break
            try:
                value, end = decoder.raw_decode(text, start)
                documents.append(value)
                offset = end
            except ValueError:
                offset = start + 1
        return documents

    def _nested(self, value, depth):
        self.node_count += 1
        if self.node_count > MAX_NODES:
            raise ValueError(f"数据节点超过 {MAX_NODES:,}，请拆分后解析。")
        if depth > 100:
            raise ValueError("嵌套内容超过 100 层。")
        if isinstance(value, dict):
            return {key: self._nested(child, depth + 1) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._nested(child, depth + 1) for child in value]
        if isinstance(value, str) and self._looks_structured(value):
            parsed, detected = self._parse(value.strip(), 0)
            if not isinstance(parsed, str):
                self.steps.append(f"展开内嵌字符串：{detected}")
                return self._nested(parsed, depth + 1)
        return value

    def _normalize(self, value, depth):
        self.node_count += 1
        if self.node_count > MAX_NODES:
            raise ValueError(f"数据节点超过 {MAX_NODES:,}，请拆分后解析。")
        if depth > 150:
            raise ValueError("数据超过 150 层，请拆分后解析。")
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                normalized_key = key if isinstance(key, str) else str(key)
                if not isinstance(key, str):
                    self.warnings.append("非字符串字典键已转换为字符串。")
                if normalized_key in result:
                    self.warnings.append(f"键转换后冲突 {normalized_key!r}：保留最后一个值。")
                result[normalized_key] = self._normalize(child, depth + 1)
            return result
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self._normalize(child, depth + 1) for child in value]
        if isinstance(value, float) and not math.isfinite(value):
            if self.strict:
                raise ValueError("数值超出有限浮点数范围，无法输出为标准 JSON。")
            self.warnings.append("非有限数值转换为 null。")
            return None
        if isinstance(value, (datetime.date, datetime.datetime)):
            return value.isoformat()
        if isinstance(value, bytes):
            self.warnings.append("二进制字面量转换为 Base64 字符串。")
            return base64.b64encode(value).decode("ascii")
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        self.warnings.append(f"{type(value).__name__} 转换为字符串。")
        return str(value)


def parse_text(source: str, strict: bool = False, decode_nested: bool = False) -> ParseResult:
    return CompatibleParser(strict, decode_nested).parse(source)
