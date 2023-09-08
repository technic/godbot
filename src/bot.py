from io import BytesIO
import os
import re
from typing import Optional, Tuple
from dataclasses import dataclass
import logging
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, ParseMode, Update
from telegram.ext import CommandHandler, Updater, CallbackContext, MessageHandler, Filters, CallbackQueryHandler
from semver import Version
from pprint import pformat
import json
from dotenv import load_dotenv
from enum import Flag, auto

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.DEBUG)
logger = logging.getLogger(__name__)


class OutputKind(Flag):
    ASM = auto()
    OUTPUT = auto()
    ALL = ASM | OUTPUT


MESSAGE_LIMIT = 1


@dataclass
class CompileResult:
    ok: bool
    header: str
    asm: list[str]
    output: list[str]

    def to_messages(self, kind: OutputKind):
        w = MessageWriter()
        w.add_line(self.header)

        if kind & OutputKind.ASM:
            if not self.asm:
                w.add_line('*Assembly*: void')
            else:
                w.add_line('*Assembly:*')
                w.set_code_mode()
                for line in self.asm:
                    w.add_line(line)
                w.set_plain_mode()

        if kind & OutputKind.OUTPUT:
            if not self.output:
                w.add_line('*Output*: void')
            else:
                w.add_line('*Output*:')
                w.set_code_mode()
                for line in self.output:
                    w.add_line(line)
                w.set_plain_mode()

        logger.info(f"Plain: {w.messages}")
        return w.messages


class MessageStore:
    def __init__(self) -> None:
        self._compiler_requests = {}
        self._compiler_results = {}

    def add_request(self, key: Tuple[int, int], request: str) -> None:
        self._compiler_requests[key] = request

    def get_request(self, key: Tuple[int, int]) -> Optional[str]:
        return self._compiler_requests.get(key)

    def add_result(self, key: Tuple[int, int], result: CompileResult):
        self._compiler_results[key] = result

    def get_result(self, key):
        return self._compiler_results.get(key)


class MessageWriter:
    def __init__(self, max_size: int = 4096) -> None:
        self.max_size = max_size
        self.messages = [""]
        self.code_mode = False

    def add_line(self, line: str) -> None:
        # Split line in parts not exceeding max_size
        line = line + "\n"
        while len(line) > self.max_size:
            self._add_block(line[:self.max_size])
            line = line[self.max_size:]
        self._add_block(line)

    def set_code_mode(self) -> None:
        self.messages[-1] += "```\n"
        self.code_mode = True

    def set_plain_mode(self) -> None:
        self.messages[-1] += "```\n"
        self.code_mode = False

    def _add_block(self, line: str) -> None:
        if len(self.messages[-1]) + len(line) > self.max_size:
            if self.code_mode:
                self.messages[-1] += "```\n"
            self.messages.append("")
            if self.code_mode:
                self.messages[-1] += "```\n"
        self.messages[-1] += line


def lines_output(output):
    return [escape_ansi(line['text']) for line in output]


def escape_ansi(text):
    """Remove ANSI escape codes"""
    text = re.sub(r'\x1b\[([\d;]*?)m', '', text)
    text = text.replace('\x1b[K', '')
    return text


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
    text += "Alternatively format like /gcc_10_1 is supported\n"
    text += "/show Shows source code from the godbolt link\n"
    text += "/showimg Displays source code from the godbolt link\n"
    logger.info(text)
    update.message.reply_html(
        text, reply_to_message_id=update.message.message_id)


def run_compiler(code, options) -> CompileResult:
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

    return CompileResult(
        ok=reply['code'] == 0,
        header=f'{compiler} {args} ' +
        ('❌' if reply['code'] != 0 else '✅'),
        asm=lines_output(reply['asm']), output=lines_output(reply['stderr']))


def compile(update: Update, context):
    """Compile the user's code using the Godbolt Compiler Explorer."""
    message = update.message or update.edited_message

    if message.reply_to_message is not None:
        cmdline = message.text.split('\n', maxsplit=1)[0]
        code = message.reply_to_message.text
        code_message = message.reply_to_message
    else:
        cmdline, code = message.text.split('\n', maxsplit=1)
        code_message = message
    logger.info(
        f"Get code from message {code_message.message_id} in chat {code_message.chat.id}")

    args = cmdline.split(maxsplit=1)
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
        message.reply_text(
            "MSVC is not a compiler", reply_to_message_id=message.message_id)
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

    if message.reply_to_message is not None:
        store.add_request((message.reply_to_message.message_id,
                          message.reply_to_message.chat.id), json.dumps(options))

    result = run_compiler(code, options)
    for msg in result.to_messages(OutputKind.ALL)[:MESSAGE_LIMIT]:
        reply = message.reply_markdown(
            msg, reply_to_message_id=code_message.message_id)
        store.add_result((reply.message_id, reply.chat_id), result)


def edited(update: Update, context: CallbackContext):
    """Handle edited messages which were replied"""
    payload = store.get_request(
        (update.edited_message.message_id, update.edited_message.chat.id))
    if not payload:
        return
    options = json.loads(payload)
    result = run_compiler(update.edited_message.text, options)
    for msg in result.to_messages(OutputKind.ALL)[:MESSAGE_LIMIT]:
        update.edited_message.reply_markdown(
            msg, reply_to_message_id=update.edited_message.message_id)


def button_pressed(update: Update, context):
    query = update.callback_query
    query.answer()

    result = store.get_result(
        (query.message.message_id, query.message.chat_id))
    if result is None:
        return

    if query.data == 'asm':
        flag = OutputKind.ASM
    else:
        flag = OutputKind.OUTPUT

    query.edit_message_text(
        text=result.to_messages(flag)[0], reply_markup=create_keyboard(), parse_mode=ParseMode.MARKDOWN)


def create_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='asm', callback_data='asm'),
         InlineKeyboardButton(text='output', callback_data='output'),
         ]
    ])


def show_link_contents(update: Update, context: CallbackContext, image=False):
    """Display code from godblot.org links"""

    links = re.findall(
        r'https://(\w+\.)?godbolt.org/z/(\w+)', update.message.text)
    if not links and update.message.reply_to_message:
        links = re.findall(
            r'https://(\w+\.)?godbolt.org/z/(\w+)', update.message.reply_to_message.text)
    if not links:
        return

    logger.info(f"Links: {links}")
    link = links[0][1]

    r = requests.get(f'https://godbolt.org/api/shortlinkinfo/{link}')
    reply = r.json()
    logger.debug(pformat(reply))

    code = reply['sessions'][0]['source']

    if not image:
        result = f'*// Code*:\n```\n{code}```\n'
        logger.info("Plain:\n" + result)
        update.message.reply_markdown(
            result, reply_to_message_id=update.message.message_id)
    else:
        update.message.reply_photo(
            generate_image(code), reply_to_message_id=update.message.message_id)


def show_link_contents_img(update: Update, context: CallbackContext):
    show_link_contents(update, context, image=True)


def render_to_image(update: Update, context: CallbackContext):
    """Render code to an image using carbonara api"""
    if not update.message.reply_to_message:
        update.message.reply_text(
            "Reply to a message with code to render it to an image")
        return

    for e in update.message.reply_to_message.parse_entities([MessageEntity.CODE, MessageEntity.PRE]):
        code = update.message.reply_to_message.text[e.offset:e.offset+e.length]
        break
    else:
        code = update.message.reply_to_message.text

    update.message.reply_photo(
        photo=generate_image(code), reply_to_message_id=update.message.message_id)


def generate_image(code: str):
    logger.info(f"Rendering code:\n{code}")
    r = requests.post('https://carbonara.solopov.dev/api/cook',
                      json={'code': code, 'theme': 'one-dark', 'language': 'text/x-c++src',
                            'paddingVertical': '10px', 'paddingHorizontal': '10px'})
    r.raise_for_status()
    return BytesIO(r.content)


def error(update: object, context: CallbackContext) -> None:
    logger.warning('Update "%s" caused error "%s"', update, context.error)


# Define and start the bot
def main() -> None:
    updater = Updater(token=os.environ['TELEGRAM_TOKEN'], use_context=True)
    dispatcher = updater.dispatcher

    # Add commands for each compiler
    for compiler in cr.compilers:
        logging.info(f'Adding command {compiler.command} - {compiler.title}')
        dispatcher.add_handler(CommandHandler(
            compiler.command, compile, filters=Filters.text))

    dispatcher.add_handler(MessageHandler(
        callback=edited, filters=Filters.text & Filters.update.edited_message))
    dispatcher.add_handler(CallbackQueryHandler(button_pressed))

    # Add command for links
    dispatcher.add_handler(CommandHandler(
        'show', show_link_contents, filters=Filters.text))

    dispatcher.add_handler(CommandHandler(
        'showimg', show_link_contents_img, filters=Filters.text))

    # Add command for rendering code to image
    dispatcher.add_handler(CommandHandler(
        'img', render_to_image, filters=Filters.reply & Filters.text))

    # Add help command
    dispatcher.add_handler(CommandHandler('help', help))

    dispatcher.add_error_handler(error)

    # Start the bot
    if os.getenv('APP_ENVIRONMENT', '') == 'dev':
        logger.info('Start polling')
        updater.start_polling()
    else:
        logger.info('Starting webhook')
        hook = os.environ['TELEGRAM_HOOK']
        updater.start_webhook('0.0.0.0', port=8080, url_path=hook,
                              webhook_url=f'https://godbot.fly.dev/{hook}')
    updater.idle()


class Compiler:
    def __init__(self, id: str, ver: str, title: str, command: Optional[str] = None) -> None:
        self.id = id
        self.title = title
        self.command = self.clean_command(command if command else id)
        self.name = self.get_name(id)
        try:
            self.ver = Version.parse(ver, optional_minor_and_patch=True)
        except ValueError:
            self.ver = ver

    @staticmethod
    def get_name(compiler: str) -> Optional[str]:
        if re.match(r'g\d+', compiler):
            return 'gcc'
        elif re.match(r'clang\d+', compiler):
            return 'clang'
        else:
            return None

    @staticmethod
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
        # Add more friendly aliases
        self._add_complier_aliases()

    def _add_latest_compiler(self, name: str):
        latest = None
        for compiler in self.compilers:
            if compiler.name == name and isinstance(compiler.ver, Version) \
                    and (latest is None or compiler.ver > latest.ver):
                latest = compiler
        if latest:
            self.compilers.append(
                Compiler(id=latest.id, ver='(latest)', title=latest.title, command=name))
            
    def _add_complier_aliases(self):
        aliases = []
        for c in self.compilers:
            if isinstance(c.ver, Version):
                t = c.ver.to_tuple()
                if not all(part is None for part in t[3:]):
                    continue

                ver_parts = []
                for part in t[:3]:
                    if part == 0:
                        break
                    ver_parts.append(str(part))
                if not ver_parts:
                    continue

                aliases.append(Compiler(id=c.id, ver=str(c.ver), title=c.title, command=f'{c.name}-{"_".join(ver_parts)}'))
        
        self.compilers += aliases

    def get_compiler_by_command(self, command: str):
        for compiler in self.compilers:
            if compiler.command == command:
                return compiler
        raise ValueError(f"Invalid compiler command: {command}")

    def get_compiler(self, name: str, version: str) -> Compiler:
        # check if version is semver
        try:
            ver = Version.parse(version)
        except ValueError:
            ver = version

        if isinstance(ver, str):
            # find compiler with the same name and version
            return self.get_compiler_exact(name, ver)
        else:
            maxVer = ver.bump_patch()
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
            ver = Version.parse(version)
        except ValueError:
            ver = version

        # find compiler with the same name and version
        for compiler in self.compilers:
            if compiler.name == name and compiler.ver == ver:
                return compiler
        else:
            raise ValueError(f"Compiler {name}-{ver} not found")


if __name__ == '__main__':
    load_dotenv()
    cr = CompilerRegistry()
    cr.load()
    store = MessageStore()
    main()
