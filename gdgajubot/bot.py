#!/usr/bin/env python3
"""Bot do GDG-Aracaju."""
import datetime
import functools
import itertools
import logging
import random
import re
import textwrap
from collections import OrderedDict
from threading import RLock

import telegram
from telegram.ext import CommandHandler, Updater
from telegram.ext.filters import BaseFilter

from .data.resources import Resources
from .decorators import *
from .util import extract_command, AJU_TZ


class AdminFilter(BaseFilter):
    # Filtro para identificar se o comando recebido vem de um admin
    def __init__(self, command, resources):
        self.command = command
        self.resources = resources

    def filter(self, message):
        is_match = re.match(r'^/%s(?:\s|$)' % self.command, message.text)

        if not is_match:
            return False

        if self.resources.is_user_admin(message.from_user.id):
            logging.info("Comando administrativo chamado: /%s", self.command)
            return True
        else:
            message.reply_text("Você não é meu mestre para me dar ordens 😤", quote=True)
            return False


# Alias para reutilizar o cache como decorator
cache = Resources.cache


# Adapta a assinatura de função esperada por `add_handler` na API nova
def adapt_callback(cb, *args, **kwargs):
    if args:
        cb = functools.partial(cb, *args, **kwargs)
    return lambda _, u, *args, **kwargs: cb(u.message, *args, **kwargs)


ALREADY_ANSWERED_TEXTS = (
    "Ei, olhe, acabei de responder!",
    "Me reservo ao direito de não responder!",
    "Deixe de insistência!",
)

TIME_LEFT = OrderedDict([
    (30, '30 segundos'),
    (60, '1 minuto'),
    (600, '10 minutos'),
    (1800, 'meia hora'),
    (3600, '1 hora'),
])


class GDGAjuBot:
    def __init__(self, config, bot=None, resources=None):
        self.config = config
        self.resources = resources if resources else Resources(config)
        self.state_access = dict(
            count=0,
            lock=RLock()
        )
        self.states = self.resources.load_states()
        self.clear_stale_states(as_task=False)

        # O parâmetro bot só possui valor nos casos de teste, nesse caso,
        # encerra o __init__ aqui para não haver conexão ao Telegram.
        if bot:
            self.bot = bot
            return

        # Conecta ao telegram com o token passado na configuração
        self.updater = Updater(token=config.telegram_token)
        self.bot = self.updater.bot

        # Anexa uma função da API antiga para manter retrocompatibilidade
        self.bot.reply_to = lambda message, text, **kwargs: \
            self.bot.send_message(
                chat_id=message.chat_id, text=text,
                reply_to_message_id=message.message_id, **kwargs
            )

        dispatcher = self.updater.dispatcher

        # Configura os comandos aceitos pelo bot
        command.process(self)

        # Configura os comandos personalizados
        if self.config.custom_responses:
            for cmd, response in self.config.custom_responses.items():
                name = cmd.replace('/', '')
                custom = functools.partial(
                    adapt_callback(self.custom_response_template),
                    command=name, response_text=response
                )
                dispatcher.add_handler(
                    CommandHandler(name, custom)
                )

        # Configura as funções que reagem a todas as mensagens de texto
        on_message.process(self)

        # Configura as tasks
        task.process(self)

    def custom_response_template(
        self, message, *args, command='', response_text=''
    ):
        logging.info(command)
        self.bot.reply_to(message, response_text)

    def get_state(self, state_id, chat_id):
        state = self.states[state_id][chat_id]

        if 'chat' not in state:
            state['chat'] = self.bot.get_chat(chat_id).username

        return state

    @command('/start')
    def send_welcome(self, message):
        """Mensagem de apresentação do bot."""
        logging.info("/start")
        start_message = "Olá! Eu sou o bot para %s! Se precisar de ajuda: /help" % (
            ', '.join(self.config.group_name))
        self.bot.reply_to(message, start_message)

    @command('/help')
    def help(self, message):
        """Mensagem de ajuda do bot."""
        logging.info("/help")
        help_message = "/help - Exibe essa mensagem.\n" \
            "/about - Sobre o bot e como contribuir.\n" \
            "/book - Informa o ebook gratuito do dia na Packt Publishing.\n"
        if len(self.config.group_name) > 1:
            help_message += "/events - Informa a lista de próximos eventos dos grupos: {group_name}."
        else:
            help_message += "/events - Informa a lista de próximos eventos do {group_name}."

        self.bot.reply_to(
            message,
            help_message.format(
                group_name=', '.join(self.config.group_name))
        )

    @command('/links')
    def links(self, message):
        """Envia uma lista de links do grupo associado."""
        logging.info("/links")
        social_links = self.config.links
        if social_links:
            response = '*Esses são os links para o nosso grupo:*\n\n'
            for link_type, link_url in social_links.items():
                response += "🔗 {type}: {url}\n".format(
                    type=link_type.capitalize(),
                    url=link_url
                )
        else:
            response = 'Não existem links associados a esse grupo.'
        self._send_smart_reply(
            message, response,
            parse_mode="Markdown", disable_web_page_preview=True)

    @command('/events')
    def list_upcoming_events(self, message):
        """Retorna a lista de eventos do Meetup."""
        logging.info("%s: %s", message.from_user.name, "/events")
        try:
            next_events = self.resources.get_events(5)
            if next_events:
                response = self._format_events(next_events)
            else:
                response = "Não há nenhum futuro evento do grupo {0}.".format(
                    self.config.group_name)
            self._send_smart_reply(
                message, response,
                parse_mode="Markdown", disable_web_page_preview=True
            )
        except Exception as e:
            logging.exception(e)

    def _format_events(self, events):
        response = []
        for event in events:
            # If the events wasn't in cache, event['time'] is a datetime object
            # So we format it!
            if isinstance(event['time'], datetime.datetime):
                # create a pretty-looking date
                formatting = '%d/%m %Hh'
                if event['time'].minute:
                    formatting += '%M'
                event['time'] = event['time'].strftime(formatting)

            response.append("[%(name)s](%(link)s): %(time)s" % event)
        return '\n'.join(response)

    @on_message('.*')
    def extract_and_save_data(self, message, *args, **kwargs):
        self.resources.log_message(message, *args, **kwargs)

    @on_message('.*')
    def chat_statistics(self, message):
        stats = self.get_state('chat_stats', message.chat_id)
        stats['last_activity'] = datetime.datetime.now(AJU_TZ)

    @task(once=60)
    @on_message('.*')
    def ensure_daily_book(self, message=None, as_job=False):
        # without message, it dispatches the function for each chat state!
        if not message:
            if len(self.states['daily_book']) > 0:
                new_message = functools.partial(telegram.Message, 0, self.get_me(), datetime.datetime.now())
                for chat_id, state in self.states['daily_book'].items():
                    self.ensure_daily_book(new_message(self.bot.get_chat(chat_id)), as_job=True)
            return

        state = self.get_state('daily_book', message.chat_id)
        schedule_job = self.__daily_book_scheduler(state, message)

        # when function isn't called as a job, only count the message.
        if not as_job:
            count = state.get('messages_since', 0)
            count += 1
            state['messages_since'] = count

            logging.info("ensure_daily_book: %s count=%d last=%s", message.chat.username, count, state.get('last_time'))

            # the rest of the function is executed when called as a job
            return

        # when called as a job and is a dispatching, avoid duplicate
        elif message.from_user == self.get_me() and 'first_call' not in state['__memory__']:
            state['__memory__']['first_call'] = True
            return

        # that is first message coming from this chat_id: reschedule a job to 3 hours from now
        if 'last_time' not in state:
            state['last_time'] = datetime.datetime.now(tz=AJU_TZ)
            schedule_job(3 * 3600)

        # otherwise: make checks to send the book
        else:
            count = state['messages_since']
            last = state['last_time']
            now = datetime.datetime.now(tz=AJU_TZ)
            passed = now - last

            # how many hours in an interval to check the book again
            def between(h1, h2):
                return random.randint(h1, h2) * 3600

            # consider to send only if has passed at least 3 hours since last sent book
            if passed.days == 0 and passed.seconds < 3 * 3600:
                schedule_job(between(3, 12))  # reschedule a job to some hours from now
                return

            should_send = (                                     # we should send if
                passed.days >= 1                                # has passed 1 day or more since last book was sent
                or count >= 25 and passed.seconds >= 12 * 3600  # passed 25 messages and 12 hours or more
                or count >= 100 and passed.seconds >= 6 * 3600  # passed 100 messages and 6 hours or more
                or count >= 300                                 # passed 300 messages and 3 hours or more
            )

            # book should be sent now
            if should_send:
                self.warn_auto_message(message.chat_id)
                self.packtpub_free_learning(message, now, reply=False)
                logging.info("ensure_daily_book: sent to %s", message.chat.username)
                schedule_job(between(12, 24))  # reschedule a job to a bunch of hours from now
            # or no sending at the moment
            else:
                hours = passed.seconds // 3600
                schedule_job(between(1, 24 - hours))  # reschedule a job to a fair time

    def __daily_book_scheduler(self, state, message):
        my = state['__memory__']

        if 'schedule_fn' in my:
            return my['schedule_fn']

        with self.state_access['lock']:
            if 'schedule_fn' in my:  # avoiding possibility of duplicated work
                return my['schedule_fn']

            cb = lambda bot, job, msg=message: self.ensure_daily_book(msg, as_job=True)

            def schedule_job(seconds, to_log=True, job_callback=cb, chat_name=message.chat.username):
                self.updater.job_queue.run_once(job_callback, when=seconds)
                if to_log:
                    logging.info("ensure_daily_book: %s scheduled to %d hours from now", chat_name, seconds // 3600)

            my['schedule_fn'] = schedule_job

            # there is no daily book job yet: schedule it now!
            schedule_job(60, to_log=False)

            return schedule_job

    @task(daily=datetime.time(0, 0))
    def clear_stale_states(self, as_task=True):
        if as_task:
            logging.info("Clearing stale chats states")
            self.dump_states()

        now = datetime.datetime.now(AJU_TZ)
        all_stats = self.states['chat_stats']
        staled_chats = set()

        for state_id, chat_states in self.states.items():
            for chat_id in chat_states:
                if chat_id in staled_chats:
                    continue

                if chat_id not in all_stats:
                    staled_chats.add(chat_id)
                    continue

                stats = all_stats[chat_id]
                if 'last_activity' not in stats or (now - stats['last_activity']).days >= 1:
                    staled_chats.add(chat_id)

        for chat_id, states in itertools.product(staled_chats, self.states.values()):
            states.pop(chat_id, None)

    @task(each=600)
    @command('/dump_states', admin=True)
    def dump_states(self, message=None):
        if message:
            self.bot.reply_to(message, "Despejo de memória acionado com sucesso")

        access = self.state_access

        with access['lock']:
            if access['count'] == 0:
                return
            logging.info("Dumping bot states to the database")
            states = super().__getattribute__('states')  # get states without changing access status
            self.resources.update_states(states)
            access['count'] = 0

    def warn_auto_message(self, chat_id):
        random_text = random.choice((
            lambda: '_👾 Mensagem automática do seu bot favorito._',
            lambda: '_🤖 Mensagem automática do amigão_ [{me.name}](tg://user?id={me.id})'.format(me=self.get_me()),
        ))
        self.bot.send_message(chat_id, random_text(), parse_mode="Markdown")

    # used to keep track of self.states access
    def __getattribute__(self, name):
        access = super().__getattribute__('state_access')

        with access['lock']:
            if name == 'states':
                access['count'] += 1

        return super().__getattribute__(name)

    @command('/book')
    def packtpub_free_learning(self, message, now=None, reply=True):
        """Retorna o livro disponível no free-learning da editora PacktPub."""
        if reply:
            logging.info("%s: %s", message.from_user.name, "/book")
            send_message = self._send_smart_reply
        else:
            send_message = self.send_text_photo

        if now is None:
            now = datetime.datetime.now(tz=AJU_TZ)

        book, response = self.__get_book(now)

        cover = book['cover'] if book else None

        has_sent = send_message(
            message, response,
            parse_mode="Markdown", disable_web_page_preview=True,
            picture=cover
        )

        if has_sent:
            state = self.get_state('daily_book', message.chat_id)
            state['last_time'] = now
            state['messages_since'] = 0
            state.dump()

    def __get_book(self, now):
        try:
            book = self.resources.get_packt_free_book()

            summary = textwrap.shorten(book.summary, 150, placeholder=r' \[...]')
            response = (
                "Confira o livro gratuito de hoje da Packt Publishing 🎁\n\n"
                "📖 [%s](%s)\n"
                "🔎 %s\n"
            ) % (book.name, Resources.BOOK_URL, summary)

            return book, response
        except Exception:
            # As tentativas falharam...
            Resources.cache.invalidate(Resources.get_packt_free_book, "get_packt_free_book")
            book = None
            response = "Parece que não tem um livro grátis hoje 😡\n\n" \
                       "Se acha que é um erro meu, veja com seus próprios olhos em " + Resources.BOOK_URL

        return book, response

    def send_text_photo(self, message, text, picture=None, reply_to=False, **kwargs):
        if reply_to:
            kwargs['reply_to_message_id'] = message.message_id

        if picture:
            self.bot.send_photo(message.chat_id, photo=picture, **kwargs)
            if reply_to:
                del kwargs['reply_to_message_id']

        return self.bot.send_message(message.chat_id, text, **kwargs)

    def _send_smart_reply(self, message, text, picture=None, **kwargs):
        send_message = functools.partial(self.send_text_photo, message, text, picture,
                                         reply_to=True, **kwargs)

        # On groups or supergroups, check if I have
        # a recent previous response to refer
        if message.chat.type in ["group", "supergroup"]:
            # Retrieve from cache and set if necessary
            key = "p%s" % extract_command(text)
            previous_cache = Resources.cache.get_cache(key, expire=600)
            previous = previous_cache.get(key=message.chat.id, createfunc=dict)

            # Verify if previous response is the same
            # to send a contextual response
            if previous.get('text') == text:
                self.bot.send_message(
                    message.chat.id, '👆 ' + random.choice(ALREADY_ANSWERED_TEXTS),
                    reply_to_message_id=previous['message_id']
                )
                return False

            # or, send new response and update the cache
            else:
                sent = send_message()
                previous.update({'text': text, 'message_id': sent.message_id})
                previous_cache[message.chat.id] = previous  # reset expire time

        # On private chats or channels, send the normal reply...
        else:
            send_message()

        return True

    @command('/about')
    def about(self, message):
        logging.info("%s: %s", message.from_user.name, "/about")
        response = "Esse bot obtém informações de eventos do Meetup ou Facebook. "
        response += "Para saber mais ou contribuir: https://github.com/GDGAracaju/GDGAjuBot/"
        self.bot.send_message(message.chat.id, response)

    @command('/list_users', admin=True)
    def list_users(self, message):
        users = self.resources.list_all_users()
        response = '\n'.join([str(user) for user in users])
        self.bot.send_message(message.chat.id, response)

    @easter_egg(r"(?i)\bRUBY\b")
    def love_ruby(self, message):
        """Easter Egg com o Ruby."""
        logging.info("%s: %s", message.from_user.name, "ruby")
        username = message.from_user.name
        self.bot.send_message(
            message.chat.id,
            "{} ama Ruby... ou Rails?".format(username),
        )

    @easter_egg(r"(?i)\bJAVA\b")
    def memory_java(self, message):
        """Easter Egg com o Java."""
        logging.info("%s: %s", message.from_user.name, "java")
        self.bot.send_message(message.chat.id, "Ihh... acabou a RAM")

    @easter_egg(r"(?i)\bPYTHON\b")
    def easter_python(self, message):
        """Easter Egg com o Python."""
        logging.info("%s: %s", message.from_user.name, "python")
        self.bot.send_message(message.chat.id, "import antigravity")

    def get_me(self):
        try:
            return self.__get_me
        except AttributeError:
            self.__get_me = self.bot.get_me()
            return self.__get_me

    def start(self):
        self.updater.start_polling(clean=True)
        logging.info("GDGAjuBot iniciado")
        logging.info("Este é o bot do %s", self.config.group_name)
        if self.config.debug_mode:
            logging.info("Modo do desenvolvedor ativado")
            logging.info("Usando o bot %s", self.get_me().name)
            logging.info(
                "Usando telegram_token=%s", self.config.telegram_token)
            logging.info(
                "Usando meetup_key=%s", self.config.meetup_key)
