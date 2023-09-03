from ..src.bot import convert_ansi


def test_convert_ansi():
    """Test ANSI escape code conversion"""
    # Test 1: no escape codes
    text = "Hello, world!"
    assert convert_ansi(text) == text

    # Test 2: one escape code
    text = "Hello, \x1b[31mworld!"
    html = "Hello, <span style=\"color: #ff0000\">world!</span>"
    assert convert_ansi(text) == html

    # Test 3: multiple escape codes
    text = "Hello, \x1b[31m\x1b[1mworld!"
    html = "Hello, <span style=\"color: #ff0000; font-weight: bold\">world!</span>"
    assert convert_ansi(text) == html

    # Test 4: multiple escape codes with reset
    text = "Hello, \x1b[31m\x1b[1mworld!\x1b[0m"
    html = "Hello, <span style=\"color: #ff0000; font-weight: bold\">world!</span>"
    assert convert_ansi(text) == html

    test = "# Compilation provided by Compiler Explorer at https://godbolt.org/\n<Compilation failed>\n# Compiler exited with result code 1\nStandard error:\n\x1b[01m\x1b[K<source>:1:1:\x1b[m\x1b[K \x1b[01;31m\x1b[Kerror: \x1b[m\x1b[Kexpected unqualified-id before '\x1b[01m\x1b[K/\x1b[m\x1b[K' token\n    1 | \x1b[01;31m\x1b[K/\x1b[m\x1b[Kcompile\n      | \x1b[01;31m\x1b[K^\x1b[m\x1b[K\n"
    print(convert_ansi(test))
