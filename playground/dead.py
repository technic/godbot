def convert_ansi(text):
    """Convert ANSI escape codes to HTML"""
    # escape html tags
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # find all ANSI escape codes
    escape_pattern = re.compile(r'\x1b\[([\d;]*?)m')

    # table which maps ANSI escape codes to HTML attributes
    ansi_to_html = {
        1: 'font-weight: bold',
        4: 'text-decoration: underline',
        7: 'text-decoration: reverse',
        30: 'color: #000000',
        31: 'color: #ff0000',
        32: 'color: #00ff00',
        33: 'color: #ffff00',
        34: 'color: #0000ff',
        35: 'color: #ff00ff',
        36: 'color: #00ffff',
        37: 'color: #ffffff',
        40: 'background-color: #000000',
        41: 'background-color: #ff0000',
        42: 'background-color: #00ff00',
        43: 'background-color: #ffff00',
        44: 'background-color: #0000ff',
        45: 'background-color: #ff00ff',
        46: 'background-color: #00ffff',
        47: 'background-color: #ffffff',
    }

    fragments = []
    params = []
    start = 0
    for match in escape_pattern.finditer(text):
        # Add the text fragment between the previous match and this one
        plain_text = text[start:match.start()]
        fragments.append((params, plain_text))
        # Process the escape code for next fragment

        def proces_param(param):
            return int(param) if param else 0
        params = [proces_param(s) for s in match.group(1).split(';')]
        # Update the start index for the next fragment
        start = match.end()

    # Add the final text fragment
    fragments.append((params, text[start:]))

    logger.info(f"ANSI fragments: {fragments}")
    print(fragments)

    html = ""
    html_attrs = []
    for params, plain_text in fragments:
        if 0 in params or params == []:
            # Reset all attributes
            html_attrs = []
        else:
            # Convert the escape code parameters to HTML attributes
            html_attrs += [ansi_to_html[param]
                           for param in params if param in ansi_to_html]

        if not plain_text:
            # Skip empty fragments
            continue
        # Add the text fragment to the output
        if html_attrs:
            html += f'<span style="{"; ".join(html_attrs)}">{plain_text}</span>'
        else:
            html += plain_text
    return html
