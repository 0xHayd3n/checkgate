"""Data-handling repair tasks, with protected tests and independent oracles.

Reference repairs are used only to validate fixtures, never as agent input.
"""

CSV_TASK = """Repair aggregate_amounts(csv_text) in ledger.py. It receives a CSV string and returns a dict mapping category strings to exact decimal.Decimal totals. Use the standard-library csv parser and Decimal arithmetic, with no float conversion. Ignore completely blank records and records whose cells are all whitespace, including before the header. The first nonblank record must contain exactly the two distinct headers category and amount, in either order; surrounding header whitespace is ignored. Remaining nonblank records must have exactly two cells. Strip surrounding whitespace from categories and amounts; an empty category or amount is invalid. Honor ordinary CSV quoting, including commas, doubled quotes, and newlines inside quoted fields. Amounts may be signed decimal values or exponent notation, but must be finite; invalid headers, missing headers, malformed row widths, empty required cells, invalid amounts, and nonfinite amounts must raise ValueError. Preserve every category encountered, even if its total is zero. Header-only input returns an empty dict. Do not change the tests or add dependencies."""

CSV_BROKEN = '''from decimal import Decimal, InvalidOperation


def aggregate_amounts(csv_text):
    rows = [line.split(",") for line in csv_text.splitlines()]
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("Missing header")
    header = [cell.strip() for cell in rows[0]]
    if len(header) != 2 or set(header) != {"category", "amount"}:
        raise ValueError("Expected category and amount headers")
    category_index, amount_index = header.index("category"), header.index("amount")
    totals = {}
    for row in rows[1:]:
        if len(row) != 2:
            raise ValueError("Expected two cells")
        category, raw_amount = row[category_index].strip(), row[amount_index].strip()
        if not category or not raw_amount:
            raise ValueError("Empty required cell")
        try:
            amount = Decimal(float(raw_amount))
        except (ValueError, InvalidOperation, OverflowError) as error:
            raise ValueError("Invalid amount") from error
        if not amount.is_finite():
            raise ValueError("Amount must be finite")
        totals[category] = totals.get(category, Decimal(0)) + amount
    return {category: total for category, total in totals.items() if total}
'''

CSV_REFERENCE = '''import csv
import io
from decimal import Decimal, InvalidOperation, localcontext, MAX_EMAX, MIN_EMIN


def _add_exact(left, right):
    # Align both coefficients, leaving one extra digit for an addition carry.
    exponent = min(left.as_tuple().exponent, right.as_tuple().exponent)
    precision = max(left.adjusted(), right.adjusted()) - exponent + 2
    with localcontext() as context:
        context.prec = max(1, precision)
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        return left + right


def aggregate_amounts(csv_text):
    rows = [row for row in csv.reader(io.StringIO(csv_text, newline=""))
            if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("Missing header")
    header = [cell.strip() for cell in rows[0]]
    if len(header) != 2 or set(header) != {"category", "amount"}:
        raise ValueError("Expected category and amount headers")
    category_index, amount_index = header.index("category"), header.index("amount")
    totals = {}
    for row in rows[1:]:
        if len(row) != 2:
            raise ValueError("Expected two cells")
        category, raw_amount = row[category_index].strip(), row[amount_index].strip()
        if not category or not raw_amount:
            raise ValueError("Empty required cell")
        try:
            amount = Decimal(raw_amount)
        except (ValueError, InvalidOperation) as error:
            raise ValueError("Invalid amount") from error
        if not amount.is_finite():
            raise ValueError("Amount must be finite")
        totals[category] = _add_exact(totals.get(category, Decimal(0)), amount)
    return totals
'''

CSV_TESTS = '''import unittest
from decimal import Decimal
from ledger import aggregate_amounts


class LedgerTests(unittest.TestCase):
    def test_totals(self):
        self.assertEqual(aggregate_amounts("category,amount\\nfood,2\\nbooks,3\\nfood,4\\n"),
                         {"food": Decimal("6"), "books": Decimal("3")})

    def test_exact_decimal_arithmetic(self):
        self.assertEqual(aggregate_amounts("category,amount\\nx,0.1\\nx,0.2\\n"),
                         {"x": Decimal("0.3")})

    def test_large_values_and_exact_cancellation(self):
        cases = [
            (["12345678901234567890123456789"], "12345678901234567890123456789"),
            (["10000000000000000000000000000", "1"], "10000000000000000000000000001"),
            (["1e40", "1", "-1e40"], "1"),
            (["1e40", "1e-40", "-1e40"], "1e-40"),
            (["-99999999999999999999999999999", "-1"], "-100000000000000000000000000000"),
            (["123456789012345678901234567890.000000000001",
              "-123456789012345678901234567890"], "0.000000000001"),
        ]
        for amounts, expected in cases:
            with self.subTest(amounts=amounts):
                text = "category,amount\\n" + "".join("x," + amount + "\\n" for amount in amounts)
                self.assertEqual(aggregate_amounts(text), {"x": Decimal(expected)})

    def test_quoted_categories(self):
        self.assertEqual(aggregate_amounts('category,amount\\n"food, drink",2\\n"line\\nbreak",3\\n'),
                         {"food, drink": Decimal(2), "line\\nbreak": Decimal(3)})

    def test_blank_records_and_whitespace(self):
        self.assertEqual(aggregate_amounts("\\n , \\n category , amount \\n\\n x , 2 \\n , \\n"),
                         {"x": Decimal(2)})

    def test_negative_and_zero_totals(self):
        self.assertEqual(aggregate_amounts("category,amount\\nx,4\\nx,-4\\ny,0\\nz,-2\\n"),
                         {"x": Decimal(0), "y": Decimal(0), "z": Decimal(-2)})

    def test_reordered_header_and_exponents(self):
        self.assertEqual(aggregate_amounts("amount,category\\n1e2, x\\n-2E1,x\\n"),
                         {"x": Decimal(80)})

    def test_invalid_headers(self):
        for text in ("", "\\n , \\n", "category,cost\\n", "category,category\\n", "category,amount,x\\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                aggregate_amounts(text)

    def test_invalid_data(self):
        for row in ("x", "x,1,2", ",2", "x, ", "x,hello", "x,NaN", "x,Infinity", "x,-Infinity"):
            with self.subTest(row=row), self.assertRaises(ValueError):
                aggregate_amounts("category,amount\\n" + row + "\\n")

    def test_header_only(self):
        self.assertEqual(aggregate_amounts("category,amount\\n\\n"), {})


if __name__ == "__main__":
    unittest.main()
'''

CSV_ORACLE = '''import csv
import io
from decimal import Decimal
from ledger import aggregate_amounts


def encoded(header, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\\r\\n")
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue()


categories = ["plain", "a,b", 'say "yes"', "two\\nlines", "two\\r\\nlines", "caf\\u00e9", "x"]
rows = []
expected = {}
for index, category in enumerate(categories):
    rows.extend([(category, "0.10"), (category, "0.20"), (category, "-0.30"), (category, str(index))])
    expected[category] = Decimal(index)
result = aggregate_amounts(encoded(["category", "amount"], rows))
assert result == expected, (result, expected)
assert all(type(value) is Decimal for value in result.values())

assert aggregate_amounts(encoded([" amount ", " category "],
    [("+2.5e2", "  padded  "), ("-.5", "padded"), ("-249.5", "padded"),
     ("0.00000000000000000001", "tiny"), ("1e1000", "large")])) == {
         "padded": Decimal(0), "tiny": Decimal("1e-20"), "large": Decimal("1e1000")}
assert aggregate_amounts('\\n\\t, \\ncategory,amount\\r\\n"",""\\nzero,-0.00\\n') == {"zero": Decimal(0)}
assert aggregate_amounts("amount,category\\n") == {}

# Literal expectations do not round under the evaluator's Decimal context.
exact_cases = [
    (["12345678901234567890123456789"], "12345678901234567890123456789"),
    (["10000000000000000000000000000", "1"], "10000000000000000000000000001"),
    (["1e40", "1", "-1e40"], "1"),
    (["1e40", "1e-40", "-1e40"], "1e-40"),
    (["-99999999999999999999999999999", "-1"], "-100000000000000000000000000000"),
    (["123456789012345678901234567890.000000000001",
      "-123456789012345678901234567890"], "0.000000000001"),
]
for amounts, literal in exact_cases:
    actual = aggregate_amounts(encoded(["category", "amount"], [("exact", value) for value in amounts]))
    assert actual == {"exact": Decimal(literal)}, (amounts, literal, actual)
    assert type(actual) is dict and type(actual["exact"]) is Decimal

invalid = ["", " , \\n", "amount,amount\\n", "amount\\n", "amount,category,extra\\n"]
invalid += ["category,amount\\n" + row + "\\n" for row in
            ["missing", "many,1,extra", " ,1", "x,", "x,bad", "x,sNaN", "x,NaN", "x,+Infinity", "x,-Inf"]]
for text in invalid:
    try:
        aggregate_amounts(text)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError: " + repr(text))
'''

MERGE_TASK = """Repair merge_config(base, overrides) in config_merge.py. Both arguments must be collections.abc.Mapping objects; raise TypeError for other top-level arguments. Inputs use string keys and values consisting of nested mappings, lists, and immutable scalar values (str, int, float, bool, or None). Return a plain dict. For each override key, recursively merge only when both old and new values are mappings. Otherwise the override replaces the old value in full: lists replace lists instead of concatenating, mappings replace scalars or lists, scalars replace mappings, and None is an explicit replacement. Preserve keys absent from overrides. Do not mutate either input, and deeply detach every mutable value in the result from both inputs, including unchanged nested mappings, lists and their elements. Convert every nested Mapping in the output to a plain dict, including mappings inside lists; support read-only MappingProxyType inputs. Empty overrides still return a detached copy. Do not change the tests or add dependencies."""

MERGE_BROKEN = '''from collections.abc import Mapping


def _copy(value):
    if isinstance(value, Mapping):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def merge_config(base, overrides):
    if not isinstance(base, Mapping) or not isinstance(overrides, Mapping):
        raise TypeError("Both inputs must be mappings")
    result = dict(base)
    for key, value in overrides.items():
        if value is None:
            continue
        if key in result and isinstance(result[key], Mapping) and isinstance(value, Mapping):
            result[key] = merge_config(result[key], value)
        elif key in result and isinstance(result[key], list) and isinstance(value, list):
            result[key] = _copy(result[key]) + _copy(value)
        else:
            result[key] = _copy(value)
    return result
'''

MERGE_REFERENCE = '''from collections.abc import Mapping


def _copy(value):
    if isinstance(value, Mapping):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def merge_config(base, overrides):
    if not isinstance(base, Mapping) or not isinstance(overrides, Mapping):
        raise TypeError("Both inputs must be mappings")
    result = _copy(base)
    for key, value in overrides.items():
        if key in result and isinstance(result[key], Mapping) and isinstance(value, Mapping):
            result[key] = merge_config(result[key], value)
        else:
            result[key] = _copy(value)
    return result
'''

MERGE_TESTS = '''import copy
import unittest
from types import MappingProxyType
from config_merge import merge_config


class ConfigTests(unittest.TestCase):
    def test_nested_merge(self):
        self.assertEqual(merge_config({"db": {"host": "old", "port": 1}, "keep": True},
                                     {"db": {"host": "new"}, "added": 2}),
                         {"db": {"host": "new", "port": 1}, "keep": True, "added": 2})

    def test_scalar_mapping_replacements(self):
        self.assertEqual(merge_config({"a": 1, "b": {"old": 2}, "c": [3]},
                                     {"a": {"new": 4}, "b": False, "c": {"x": 5}}),
                         {"a": {"new": 4}, "b": False, "c": {"x": 5}})

    def test_lists_replace(self):
        self.assertEqual(merge_config({"a": [1, 2], "b": [3]}, {"a": [4], "b": []}),
                         {"a": [4], "b": []})

    def test_none_overrides(self):
        self.assertEqual(merge_config({"a": {"x": 1}, "b": [2]}, {"a": None, "b": None, "c": None}),
                         {"a": None, "b": None, "c": None})

    def test_inputs_unchanged(self):
        base, overrides = {"x": {"a": [1]}}, {"x": {"b": [2]}}
        before = copy.deepcopy((base, overrides))
        merge_config(base, overrides)
        self.assertEqual((base, overrides), before)

    def test_unchanged_values_detached(self):
        base = {"keep": {"items": [{"x": [1]}]}}
        result = merge_config(base, {})
        result["keep"]["items"][0]["x"].append(2)
        self.assertEqual(base, {"keep": {"items": [{"x": [1]}]}})

    def test_override_values_detached(self):
        overrides = {"new": [{"x": [1]}]}
        result = merge_config({}, overrides)
        result["new"][0]["x"].append(2)
        self.assertEqual(overrides, {"new": [{"x": [1]}]})

    def test_readonly_mappings_become_plain_dicts(self):
        base = MappingProxyType({"keep": MappingProxyType({"x": [1]})})
        overrides = MappingProxyType({"new": [MappingProxyType({"y": 2})]})
        result = merge_config(base, overrides)
        self.assertIs(type(result), dict)
        self.assertIs(type(result["keep"]), dict)
        self.assertIs(type(result["new"][0]), dict)

    def test_empty_and_invalid_inputs(self):
        self.assertEqual(merge_config({}, {}), {})
        for base, overrides in (([], {}), ({}, []), (None, {}), ({}, 1)):
            with self.subTest(base=base, overrides=overrides), self.assertRaises(TypeError):
                merge_config(base, overrides)


if __name__ == "__main__":
    unittest.main()
'''

MERGE_ORACLE = '''import copy
from collections.abc import Mapping
from types import MappingProxyType
from config_merge import merge_config


def mutable_ids(value):
    ids = set()
    if isinstance(value, Mapping):
        ids.add(id(value))
        for child in value.values():
            ids.update(mutable_ids(child))
    elif isinstance(value, list):
        ids.add(id(value))
        for child in value:
            ids.update(mutable_ids(child))
    return ids


def assert_plain(value):
    if isinstance(value, Mapping):
        assert type(value) is dict
        for child in value.values():
            assert_plain(child)
    elif isinstance(value, list):
        for child in value:
            assert_plain(child)


cases = [
    ({"x": {"a": 1, "b": [1]}, "y": [2]}, {"x": {"a": None, "c": 3}, "y": []},
     {"x": {"a": None, "b": [1], "c": 3}, "y": []}),
    ({"a": [1], "b": None, "c": {"x": 2}}, {"a": None, "b": {"z": [3]}, "c": [4]},
     {"a": None, "b": {"z": [3]}, "c": [4]}),
    ({"a": {"b": {"c": [1], "d": None}}}, {"a": {"b": {"c": [{"e": [2]}]}}},
     {"a": {"b": {"c": [{"e": [2]}], "d": None}}}),
    ({"untouched": [{"deep": [1, {"x": 2}]}]}, {}, {"untouched": [{"deep": [1, {"x": 2}]}]}),
    ({}, {"new": None, "flag": False, "n": 0, "s": "", "f": 1.25},
     {"new": None, "flag": False, "n": 0, "s": "", "f": 1.25}),
]
for base, overrides, expected in cases:
    before = copy.deepcopy((base, overrides))
    result = merge_config(base, overrides)
    assert result == expected, (result, expected)
    assert (base, overrides) == before
    assert not mutable_ids(result) & (mutable_ids(base) | mutable_ids(overrides))
    assert_plain(result)

shared = {"leaf": [1]}
base = MappingProxyType({"left": shared, "right": [MappingProxyType({"x": [2]})]})
overrides = MappingProxyType({"left": MappingProxyType({"extra": [MappingProxyType({"z": 3})]})})
result = merge_config(base, overrides)
assert result == {"left": {"leaf": [1], "extra": [{"z": 3}]}, "right": [{"x": [2]}]}
assert_plain(result)
assert not mutable_ids(result) & (mutable_ids(base) | mutable_ids(overrides))
result["left"]["leaf"].append(9)
result["right"][0]["x"].clear()
assert shared == {"leaf": [1]} and base["right"][0]["x"] == [2]

for invalid in (None, [], (), "", 0, False):
    for args in ((invalid, {}), ({}, invalid)):
        try:
            merge_config(*args)
        except TypeError:
            pass
        else:
            raise AssertionError("Expected TypeError for " + repr(args))
'''

TASKS = [
    {
        "id": "csv_decimal_aggregation",
        "category": "data_parsing",
        "task": CSV_TASK,
        "files": {"ledger.py": CSV_BROKEN, "test_ledger.py": CSV_TESTS},
        "editable_files": ["ledger.py"],
        "unit_modules": ["test_ledger"],
        "oracle": CSV_ORACLE,
        "reference_files": {"ledger.py": CSV_REFERENCE},
    },
    {
        "id": "immutable_config_merge",
        "category": "recursive_data",
        "task": MERGE_TASK,
        "files": {"config_merge.py": MERGE_BROKEN, "test_config_merge.py": MERGE_TESTS},
        "editable_files": ["config_merge.py"],
        "unit_modules": ["test_config_merge"],
        "oracle": MERGE_ORACLE,
        "reference_files": {"config_merge.py": MERGE_REFERENCE},
    },
]
