"""Standard-library benchmark fixtures for graph planning and HTTP retry rules.

Only ``files`` belongs in the model-visible fixture. ``oracle`` and
``reference_files`` are independent evaluation/fixture-validation material.
"""

GRAPH_BROKEN = '''def build_graph(dependencies):
    """Return outgoing edges and prerequisite counts."""
    nodes = set(dependencies)
    outgoing = {node: set() for node in nodes}
    indegree = {node: 0 for node in nodes}
    for node, required in dependencies.items():
        for dependency in required:
            if dependency not in outgoing:
                continue
            outgoing[dependency].add(node)
            indegree[node] += 1
    return outgoing, indegree
'''

GRAPH_FIXED = '''def build_graph(dependencies):
    """Return outgoing edges and prerequisite counts without changing input."""
    nodes = set(dependencies)
    for required in dependencies.values():
        nodes.update(required)
    outgoing = {node: set() for node in nodes}
    indegree = {node: 0 for node in nodes}
    for node, required in dependencies.items():
        for dependency in set(required):
            outgoing[dependency].add(node)
            indegree[node] += 1
    return outgoing, indegree
'''

PLANNER_BROKEN = '''from graph import build_graph


def topological_order(dependencies):
    outgoing, indegree = build_graph(dependencies)
    ready = sorted(node for node in indegree if indegree[node] == 0)
    ordered = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for child in sorted(outgoing[node]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if len(ordered) != len(indegree):
        raise ValueError("Dependency cycle")
    return ordered
'''

PLANNER_FIXED = '''import heapq
from graph import build_graph


def topological_order(dependencies):
    outgoing, indegree = build_graph(dependencies)
    ready = [node for node in indegree if indegree[node] == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        node = heapq.heappop(ready)
        ordered.append(node)
        for child in outgoing[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, child)
    if len(ordered) != len(indegree):
        raise ValueError("Dependency cycle")
    return ordered
'''

GRAPH_TESTS = '''import copy
import unittest
from planner import topological_order


class PlannerTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(topological_order({}), [])

    def test_dependency_only_node(self):
        self.assertEqual(topological_order({"package": ["compile"]}), ["compile", "package"])

    def test_duplicate_dependency(self):
        self.assertEqual(topological_order({"b": ["a", "a"], "a": []}), ["a", "b"])

    def test_newly_ready_node_competes_with_existing_ready_nodes(self):
        self.assertEqual(topological_order({"a": ["b"], "b": [], "c": []}), ["b", "a", "c"])

    def test_disconnected_nodes(self):
        self.assertEqual(topological_order({"z": [], "B": [], "a": []}), ["B", "a", "z"])

    def test_chain(self):
        self.assertEqual(topological_order({"c": ["b"], "a": [], "b": ["a"]}), ["a", "b", "c"])

    def test_cycles_raise(self):
        for graph in ({"a": ["a"]}, {"a": ["b"], "b": ["a"]}, {"free": [], "a": ["b"], "b": ["a"]}):
            with self.subTest(graph=graph), self.assertRaises(ValueError):
                topological_order(graph)

    def test_input_preserved(self):
        graph = {"z": ["a", "b"], "b": [], "a": []}
        original = copy.deepcopy(graph)
        self.assertEqual(topological_order(graph), ["a", "b", "z"])
        self.assertEqual(graph, original)
'''

GRAPH_ORACLE = '''import copy
import itertools
import random
from planner import topological_order


def expected_order(graph):
    nodes = sorted(set(graph).union(*(set(values) for values in graph.values())))
    for candidate in itertools.permutations(nodes):
        index = {node: offset for offset, node in enumerate(candidate)}
        if all(index[dependency] < index[node] for node, values in graph.items() for dependency in values):
            return list(candidate)
    raise ValueError("No ordering")


def verify(graph):
    snapshot = copy.deepcopy(graph)
    try:
        expected = expected_order(graph)
    except ValueError:
        try:
            topological_order(graph)
        except ValueError:
            pass
        else:
            raise AssertionError("Cycle was accepted: " + repr(graph))
    else:
        actual = topological_order(graph)
        assert actual == expected, (graph, expected, actual)
    assert graph == snapshot, "Input graph was mutated"


verify({"ship": ["bundle", "bundle"], "bundle": ["lint"], "docs": []})
verify({"aa": ["z"], "b": [], "z": []})
verify({"orphan": [], "loop": ["loop", "loop"]})
verify({"target": ["", "UPPER"], "UPPER": [""]})
randomizer = random.Random(734251)
names = ["n0", "n1", "n2", "n3", "n4"]
for _ in range(90):
    shuffled = randomizer.sample(names, len(names))
    graph = {}
    for node in shuffled:
        if randomizer.random() < 0.82:
            graph[node] = [name for name in names if randomizer.random() < 0.24]
            if graph[node] and randomizer.random() < 0.4:
                graph[node].append(graph[node][0])
    verify(graph)
'''

POLICY_BROKEN = '''RETRYABLE_STATUSES = {429, 500, 501, 502, 503, 504}
IDEMPOTENT_METHODS = {"GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"}


def should_retry(method, status, attempt, max_attempts=3, idempotency_key=None):
    if attempt >= max_attempts:
        return False
    if status not in RETRYABLE_STATUSES:
        return False
    return method.upper() in IDEMPOTENT_METHODS | {"POST", "PATCH"}
'''

POLICY_FIXED = '''RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}
IDEMPOTENT_METHODS = {"GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"}


def should_retry(method, status, attempt, max_attempts=3, idempotency_key=None):
    if attempt >= max_attempts or status not in RETRYABLE_STATUSES:
        return False
    method = method.upper()
    if method in IDEMPOTENT_METHODS:
        return True
    return method in {"POST", "PATCH"} and isinstance(idempotency_key, str) and bool(idempotency_key.strip())
'''

DELAY_BROKEN = '''def retry_delay(attempt, base_delay=1.0, max_delay=60.0, retry_after=None):
    delay = min(max_delay, base_delay * 2 ** (attempt - 1))
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    return delay
'''

DELAY_FIXED = '''def retry_delay(attempt, base_delay=1.0, max_delay=60.0, retry_after=None):
    delay = min(max_delay, base_delay)
    remaining = attempt - 1
    while remaining and 0 < delay < max_delay:
        delay = min(max_delay, delay * 2)
        remaining -= 1
    seconds = None
    if isinstance(retry_after, int) and not isinstance(retry_after, bool) and retry_after >= 0:
        seconds = retry_after
    elif isinstance(retry_after, str):
        text = retry_after.strip()
        if text and text.isascii() and text.isdecimal():
            seconds = 0
            for digit in text:
                seconds = seconds * 10 + ord(digit) - ord("0")
                if seconds >= max_delay:
                    return max_delay
    if seconds is not None:
        return min(max_delay, seconds)
    return delay
'''

RETRY_TESTS = '''import unittest
from policy import should_retry
from delay import retry_delay


class RetryTests(unittest.TestCase):
    def test_exact_retry_statuses(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.assertTrue(should_retry("GET", status, 1))

    def test_other_statuses(self):
        for status in (200, 301, 400, 401, 404, 409, 501, 505):
            with self.subTest(status=status):
                self.assertFalse(should_retry("GET", status, 1))

    def test_idempotent_methods_case_insensitive(self):
        for method in ("get", "HeAd", "PUT", "DELETE", "OPTIONS", "TRACE"):
            with self.subTest(method=method):
                self.assertTrue(should_retry(method, 503, 1))

    def test_post_patch_need_nonblank_string_key(self):
        for method in ("POST", "patch"):
            for key in (None, "", " ", 7, True):
                with self.subTest(method=method, key=key):
                    self.assertFalse(should_retry(method, 503, 1, idempotency_key=key))
            self.assertTrue(should_retry(method, 503, 1, idempotency_key=" request-7 "))
        self.assertFalse(should_retry("CONNECT", 503, 1, idempotency_key="key"))

    def test_total_attempt_limit(self):
        self.assertTrue(should_retry("GET", 503, 2, max_attempts=3))
        self.assertFalse(should_retry("GET", 503, 3, max_attempts=3))
        self.assertFalse(should_retry("POST", 503, 1, max_attempts=1, idempotency_key="key"))

    def test_capped_exponential_delay(self):
        self.assertEqual([retry_delay(n, 2, 10) for n in range(1, 6)], [2, 4, 8, 10, 10])
        self.assertEqual(retry_delay(3, 0, 10), 0)

    def test_integer_retry_after_overrides_and_is_capped(self):
        for raw, expected in ((0, 0), (7, 7), (99, 10), (" 003 ", 3), ("999", 10)):
            with self.subTest(raw=raw):
                self.assertEqual(retry_delay(2, 2, 10, raw), expected)

    def test_invalid_retry_after_uses_backoff(self):
        for raw in (None, "", "abc", "Wed, 21 Oct 2015 07:28:00 GMT", "-2", "+2", "1.5", "\uff12", 1.5, -1, True):
            with self.subTest(raw=raw):
                self.assertEqual(retry_delay(2, 2, 10, raw), 4)

    def test_zero_delay_cap(self):
        self.assertEqual(retry_delay(4, 3, 0, "5"), 0)

    def test_large_capped_retry_inputs(self):
        # Regression coverage added after the original broad benchmark run.
        cases = (
            ((1000000, 0.5, 17), 17),
            ((1000000, 0, 17), 0),
            ((1000000, 0.5, 17, "2"), 2),
            ((1, 0.5, 17, "9" * 5000), 17),
        )
        for index, (arguments, expected) in enumerate(cases):
            with self.subTest(case=index):
                self.assertEqual(retry_delay(*arguments), expected)
'''

RETRY_ORACLE = '''from policy import should_retry
from delay import retry_delay


safe = ("GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE")
allowed = (408, 429, 500, 502, 503, 504)
for method in safe + ("POST", "PATCH", "CONNECT", "PROPFIND", ""):
    for status in range(399, 510):
        for key in (None, "", "\\t\\n", "ticket", "  ticket\\t", 0, False, ["ticket"]):
            has_key = type(key) is str and len(key.strip()) > 0
            method_safe = method in safe or (method in ("POST", "PATCH") and has_key)
            for attempt, limit in ((1, 1), (1, 2), (2, 2), (2, 5), (5, 4)):
                expected = bool(method_safe and status in allowed and attempt < limit)
                actual = should_retry(method.lower(), status, attempt, limit, key)
                assert type(actual) is bool and actual == expected, (method, status, attempt, limit, key, actual)

valid = [("\\t0008\\n", 8), (12, 12), ("0", 0), (0, 0), ("000000000000000000000004", 4)]
invalid = ["+9", "-0", "7.0", "1e2", "\\u0663", "NaN", "inf", "0x10", "5 5", "", "\\t", -8, 0.0, True, False, [], {}, "Thu, 01 Jan 1970 00:00:10 GMT"]
for base, cap in ((0, 0), (0.25, 0.5), (1.5, 9.25), (4, 100), (7, 3)):
    for attempt in (1, 2, 4, 9):
        backoff = base
        for _ in range(attempt - 1):
            backoff += backoff
        backoff = min(backoff, cap)
        assert retry_delay(attempt, base, cap) == backoff
        for raw, seconds in valid:
            actual = retry_delay(attempt, base, cap, raw)
            assert actual == min(seconds, cap), (attempt, base, cap, raw, actual)
        for raw in invalid:
            actual = retry_delay(attempt, base, cap, raw)
            assert actual == backoff, (attempt, base, cap, raw, actual)
assert retry_delay(1000000, 0.5, 17) == 17
assert retry_delay(1000000, 0, 17) == 0
assert retry_delay(1000000, 0.5, 17, "2") == 2
assert retry_delay(1, 0.5, 17, "9" * 5000) == 17
'''

TASKS = [
    {
        "id": "graph_order",
        "category": "multi_file_algorithm",
        "task": (
            "Fix deterministic dependency planning in graph.py and planner.py. "
            "topological_order(dependencies) accepts a mapping of string node names to lists of prerequisite "
            "string names. Include each mapping key and every dependency-only node exactly once. "
            "Repeated prerequisites count as one edge. At every step choose the lexicographically smallest "
            "currently available node using normal Python string ordering, including nodes newly made "
            "available by the preceding step; return that list. Empty input returns []. Raise ValueError "
            "for any cycle, including a self-loop or a cycle in a disconnected component. Do not mutate "
            "the mapping or its lists. Preserve the public topological_order function. Use only the "
            "standard library and fix the underlying graph and ordering logic. Run all required checks."
        ),
        "files": {"graph.py": GRAPH_BROKEN, "planner.py": PLANNER_BROKEN, "test_planner.py": GRAPH_TESTS},
        "editable_files": ["graph.py", "planner.py"],
        "unit_modules": ["test_planner"],
        "oracle": GRAPH_ORACLE,
        "reference_files": {"graph.py": GRAPH_FIXED, "planner.py": PLANNER_FIXED},
    },
    {
        "id": "http_retry",
        "category": "multi_file_policy",
        "task": (
            "Fix pure HTTP retry functions in policy.py and delay.py without adding network requests. "
            "Preserve should_retry(method, status, attempt, max_attempts=3, idempotency_key=None) and "
            "retry_delay(attempt, base_delay=1.0, max_delay=60.0, retry_after=None). Attempts are positive "
            "integers counting completed requests from 1; max_attempts is the positive total request limit, "
            "so should_retry returns a bool and may be true only when attempt < max_attempts. Retry exactly "
            "statuses 408, 429, 500, 502, 503, 504. Method matching is case insensitive. GET, HEAD, PUT, DELETE, "
            "OPTIONS, TRACE may retry; POST and PATCH require an idempotency key that is a string nonempty "
            "after strip(); all other methods never retry. These conditions must all hold. Delay is "
            "min(max_delay, base_delay * 2**(attempt-1)); base_delay and max_delay are finite nonnegative "
            "numbers. A valid Retry-After overrides that exponential value, including a value of zero, "
            "and is capped by max_delay. Accept Retry-After only as a nonnegative Python int excluding bool, "
            "or a stripped nonempty string of ASCII decimal digits. Ignore all other values, including "
            "negative numbers, floats, signs, decimal points, non-ASCII digits and HTTP dates, and use "
            "the exponential fallback. Use only the standard library. Run all required checks."
        ),
        "files": {"policy.py": POLICY_BROKEN, "delay.py": DELAY_BROKEN, "test_retry.py": RETRY_TESTS},
        "editable_files": ["policy.py", "delay.py"],
        "unit_modules": ["test_retry"],
        "oracle": RETRY_ORACLE,
        "reference_files": {"policy.py": POLICY_FIXED, "delay.py": DELAY_FIXED},
    },
]
