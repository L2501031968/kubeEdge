import unittest

from kb.units import format_cpu, format_memory, parse_cpu, parse_memory


class ParseCpuTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(parse_cpu("1"), 1.0)
        self.assertEqual(parse_cpu("500m"), 0.5)
        self.assertEqual(parse_cpu("1500m"), 1.5)
        self.assertAlmostEqual(parse_cpu("100u"), 0.0001)

    def test_empty(self):
        self.assertEqual(parse_cpu(None), 0.0)
        self.assertEqual(parse_cpu(""), 0.0)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            parse_cpu("两个核")


class ParseMemoryTest(unittest.TestCase):
    def test_binary_suffix(self):
        self.assertEqual(parse_memory("256Mi"), 256 * 1024 ** 2)
        self.assertEqual(parse_memory("1Gi"), 1024 ** 3)

    def test_decimal_suffix(self):
        self.assertEqual(parse_memory("1G"), 1000 ** 3)
        self.assertEqual(parse_memory("512"), 512)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            parse_memory("512MB")


class FormatTest(unittest.TestCase):
    def test_cpu(self):
        self.assertEqual(format_cpu(None), "-")
        self.assertEqual(format_cpu(0), "0")
        self.assertEqual(format_cpu(0.5), "500m")
        self.assertEqual(format_cpu(2), "2")

    def test_memory(self):
        self.assertEqual(format_memory(None), "-")
        self.assertEqual(format_memory(0), "0")
        self.assertEqual(format_memory(1024 ** 3), "1Gi")
        self.assertEqual(format_memory(int(1.5 * 1024 ** 2)), "1.5Mi")
        self.assertEqual(format_memory(1000), "1000B")


if __name__ == "__main__":
    unittest.main()
