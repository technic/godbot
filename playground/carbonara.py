import requests

from io import BytesIO

from PIL import Image

code = '''
import os
import re
from dataclasses import dataclass
from typing import Optional, Union, Tuple
import logging
import requests
from telegram import Update, ParseMode, MessageEntity
from telegram.ext import CommandHandler, Updater, CallbackContext, MessageHandler, Filters
from telegram.utils.helpers import escape_markdown
from ansi2html import Ansi2HTMLConverter
from semver import Version
from pprint import pprint, pformat
import json

conv = Ansi2HTMLConverter()

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)


class MessageStore:
    def __init__(self) -> None:
        self._compiler_requests = {}

    def add_request(self, key: Tuple[int, int], request: str) -> None:
        self._compiler_requests[key] = request

    def get_request(self, key: Tuple[int, int]) -> Optional[str]:
        return self._compiler_requests.get(key)


def join_asm_output(asm_output):
    """Join asm output lines into a single string"""
    return "\n".join(line['text'] for line in asm_output)


def escape_ansi(text):
    """Remove ANSI escape codes"""
    text = re.sub(r'\x1b\[([\d;]*?)m', '', text)
    text = text.replace('\x1b[K', '')
    return text


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


def help(update: Update, context: CallbackContext):
    """Return list of available compilers"""
    text = "<u><b>Available compilers</b></u>\n"
    compilers = []
    for name in ['gcc', 'gsnapshot', 'clang', 'clang_trunk']:
        try:
            compilers.append(cr.get_compiler_by_command(name))
        except ValueError:
            continue
    for compiler in compilers:
        text += f" /{compiler.command} - {compiler.title}\n"
    text += "<b>Full list</b>: https://godbolt.org/api/compilers/c++\n"
    text += "/show Shows source code from the godbolt link\n"
    logger.info(text)
    update.message.reply_html(
        text, reply_to_message_id=update.message.message_id)


def run_compiler(code, options):
    payload = options
    payload['source'] = code

    logger.info(f"Compiling code:\n{code}")

    compiler = options['compiler']
    args = options['options']['userArguments']

    # Make a POST request to the Godbolt API
    r = requests.post(
        f'https://godbolt.org/api/compiler/{compiler}/compile', json=payload,
        headers={'Accept': 'application/json'}
    )
    reply = r.json()
    logger.debug(f"Response:\n{pformat(reply)}")

    result = f'{compiler} {args} '
    if reply['code'] != 0:
        result += '❌\n'
    else:
        result += '✅\n'

    asm = escape_ansi(join_asm_output(r.json()['asm']))
    if not asm:
        result += '*Assembly*: void'
    else:
        result += f'*Assembly:*\n```\n{asm}```\n'

    stderr = escape_ansi(join_asm_output(r.json()['stderr']))
    if not stderr:
        result += '*Output*: void'
    else:
        result += f'*Output*:\n```\n{stderr}```'

    logger.info("Plain:\n" + result)
    return result


def compile(update: Update, context):
    """Compile the user's code using the Godbolt Compiler Explorer."""
    # Get content of the message that the user replied to
    logger.info(
        f"Get code from message {update.message.reply_to_message.message_id} in chat {update.message.reply_to_message.chat.id}")

    args = update.message.text.split(maxsplit=1)
    if len(args) > 1:
        command, compiler_args = args
    else:
        command = args[0]
        compiler_args = ""

    command = command[1:]   # Remove the leading slash
    compiler = cr.get_compiler_by_command(command)
    if not compiler_args:
        compiler_args = cr.default_options.get(compiler.name, "")

    if command.startswith('vcpp') or 'msvc' in compiler.title.lower():
        update.message.reply_text(
            "MSVC is not a compiler", reply_to_message_id=update.message.message_id)
        return

    options = {
        "compiler": compiler.id,
        "options": {
            "userArguments": compiler_args,
            "compilerOptions": {},
            "filters": {
                "intel": False,
            },
            "tools": [],
            "libraries": [
                {"id": "boost", "version": "181"},
                {"id": "fmt", "version": "trunk"},
                {"id": "rangesv3", "version": "trunk"}
            ]
        },
        "lang": "c++",
        "bypassCache": False,
        "allowStoreCodeDebug": True
    }

    store.add_request((update.message.reply_to_message.message_id,
                       update.message.reply_to_message.chat.id), json.dumps(options))

    result = run_compiler(update.message.reply_to_message.text, options)
    update.message.reply_markdown(
        result, reply_to_message_id=update.message.reply_to_message.message_id)


def edited(update: Update, context: CallbackContext):
    """Handle edited messages"""
    payload = store.get_request(
        (update.edited_message.message_id, update.edited_message.chat.id))
    if not payload:
        return
    options = json.loads(payload)
    result = run_compiler(update.edited_message.text, options)
    update.edited_message.reply_markdown(
        result, reply_to_message_id=update.edited_message.message_id)


def show_link_contents(update: Update, context: CallbackContext):
    """Display code from godblot.org links"""

    links = re.findall(r'https://godbolt.org/z/(\w+)', update.message.text)
    if not links and update.message.reply_to_message:
        links = re.findall(
            r'https://godbolt.org/z/(\w+)', update.message.reply_to_message.text)
    if not links:
        return

    logger.info(f"Links: {links}")
    link = links[0]

    r = requests.get(f'https://godbolt.org/api/shortlinkinfo/{link}')
    reply = r.json()
    logger.debug(pformat(reply))

    code = reply['sessions'][0]['source']
    result = f'*Code*:\n```\n{code}```\n'
    logger.info("Plain:\n" + result)

    update.message.reply_markdown(
        result, reply_to_message_id=update.message.message_id)


def render_to_image(update: Update, context: CallbackContext):
    """Render code to an image using ray.so api"""
    if not update.message.reply_to_message:
        update.message.reply_text(
            "Reply to a message with code to render it to an image")
        return

    code = update.message.reply_to_message.text
    logger.info(f"Rendering code:\n{code}")

    r = requests.post(
        'https://ray.so/render',
        json={'code': code},
    )
    r.raise_for_status()
    update.message.reply_photo(
        r.content, reply_to_message_id=update.message.message_id)


def error(update: Update, context: CallbackContext) -> None:
    logger.warning('Update "%s" caused error "%s"', update, context.error)


# Define and start the bot
def main() -> None:
    updater = Updater(token=os.environ['TELEGRAM_TOKEN'], use_context=True)
    dispatcher = updater.dispatcher

    # Add commands for each compiler
    for compiler in cr.compilers:
        logging.info(f'Adding command {compiler.command} - {compiler.title}')
        dispatcher.add_handler(CommandHandler(
            compiler.command, compile, filters=Filters.reply & Filters.text))

    dispatcher.add_handler(MessageHandler(
        callback=edited, filters=Filters.text & Filters.update.edited_message))

    # Add command for links
    dispatcher.add_handler(CommandHandler(
        'show', show_link_contents, filters=Filters.text))

    # Add command for rendering code to image
    dispatcher.add_handler(CommandHandler(
        'img', render_to_image, filters=Filters.reply & Filters.text))

    # Add help command
    dispatcher.add_handler(CommandHandler('help', help))

    dispatcher.add_error_handler(error)

    # Start the bot
    updater.start_polling()
    updater.idle()


class Compiler:
    def __init__(self, id: str, ver: str, title: str, command: str = None) -> None:
        self.id = id
        self.title = title
        self.command = self.clean_command(command if command else id)
        self.name = self.get_name(id)
        try:
            self.ver = Version.parse(ver, optional_minor_and_patch=True)
        except ValueError:
            self.ver = ver

    @ staticmethod
    def get_name(compiler: str) -> str:
        if re.match(r'g\d+', compiler):
            return 'gcc'
        elif re.match(r'clang\d+', compiler):
            return 'clang'
        else:
            return None

    @ staticmethod
    def clean_command(command: str) -> str:
        command = re.sub(r'[^a-zA-Z0-9_]', '_', command)
        return re.sub(r'_{2,}', '_', command)

    def build_command(self):
        """Telegram bot command to chose this compiler."""
        # check if the version is a string
        if isinstance(self.ver, str):
            # ensure that ver matches format (tag)
            if self.ver.startswith('(') and self.ver.endswith(')'):
                ver = self.ver[1:-1]
                # escape version, that it contains only latin and underscore
                ver = re.sub(r'[^a-zA-Z0-9_]', '_', ver)
                # join consequent underscores
                ver = re.sub(r'_{2,}', '_', ver)
                return f'{self.name}_{ver}'
            else:
                raise ValueError(f"Invalid version: {self.ver}")
        else:
            return self.name + "".join(str(v) for v in self.ver)


class CompilerRegistry:
    def __init__(self) -> None:
        self.compilers = []
        self.default_options = {
            'gcc': '-std=gnu++20 -Wall -Wextra -O2',
            'clang': '-std=gnu++20 -Wall -Wextra -O2',
        }

    def load(self):
        # send request to https://godbolt.org/api/compilers/c++
        r = requests.get('https://godbolt.org/api/compilers/c++',
                         headers={"Accept": "application/json"})
        # pprint(r.json())

        compilers = []
        for compiler in r.json():
            if compiler['lang'] == 'c++' and compiler['instructionSet'] == 'amd64':
                compilers.append(
                    Compiler(id=compiler['id'], ver=compiler['semver'], title=compiler['name']))

        self.compilers = compilers
        # Add aliases to latest gcc and clang
        self._add_latest_compiler('gcc')
        self._add_latest_compiler('clang')

    def _add_latest_compiler(self, name: str):
        latest = None
        for compiler in self.compilers:
            if compiler.name == name and isinstance(compiler.ver, Version) and (latest is None or compiler.ver > latest.ver):
                latest = compiler
        if latest:
            self.compilers.append(
                Compiler(id=latest.id, ver='(latest)', title=latest.title, command=name))

    def get_compiler_by_command(self, command: str):
        for compiler in self.compilers:
            if compiler.command == command:
                return compiler
        raise ValueError(f"Invalid compiler command: {command}")

    def get_compiler(self, name: str, version: str) -> Compiler:
        # check if version is semver
        try:
            ver = Version(version)
        except ValueError:
            ver = version

        if isinstance(ver, str):
            # find compiler with the same name and version
            self.get_compiler_exact(name, ver)
        else:
            parts = [int(part) for part in version.split('.')]
            # bump last part of version
            parts[-1] += 1
            maxVer = Version('.'.join(parts))

            # find latest compiler matching semver spec and name
            bestVer = None
            for compiler in self.compilers:
                compiler.ver
                if compiler.name == name and compiler.ver >= ver and compiler.ver < maxVer:
                    if bestVer is None or compiler.ver > bestVer:
                        bestVer = compiler.ver
            if bestVer is not None:
                return self.get_compiler_exact(name, bestVer)
            raise ValueError(f"Compiler {name}-{ver} not found")

    def get_compiler_exact(self, name: str, version: str) -> Compiler:
        # check if version is semver
        try:
            ver = Version(version)
        except ValueError:
            ver = version

        # find compiler with the same name and version
        for compiler in self.compilers:
            if compiler.name == name and compiler.ver == ver:
                return compiler
        else:
            raise ValueError(f"Compiler {name}-{ver} not found")


if __name__ == '__main__':
    cr = CompilerRegistry()
    cr.load()
    store = MessageStore()
    main()
'''

code = '''
int main() {
  std::cout << "qqq" << std::endl;
}
'''

r = requests.post('https://carbonara.solopov.dev/api/cook',
                  json={'code': code, 'theme': 'one-dark', 'language': 'text/x-c++src'})
r.raise_for_status()
print('ready!')

# Alternatively, display the image in your application
Image.open(BytesIO(r.content)).show()
