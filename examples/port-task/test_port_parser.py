import unittest
from port_parser import parse_port


class PortTests(unittest.TestCase):
    def test_whitespace(self):
        self.assertEqual(parse_port(" 443 "), 443)

    def test_boundaries(self):
        for value in (1, 65535, "80"):
            with self.subTest(value=value):
                self.assertEqual(parse_port(value), int(value))

    def test_invalid(self):
        for value in (0, 65536, -1, True, 1.5, "", "  ", "1.5", "-80", "１２"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    parse_port(value)


if __name__ == "__main__":
    unittest.main()
