# -*- coding: utf-8 -*-
import unittest
import os
from collections import defaultdict
from datetime import datetime
from unittest import mock

from gdgajubot import bot, util
from gdgajubot.bot import GDGAjuBot, ALREADY_ANSWERED_TEXTS

AJU_TZ = util.AJU_TZ


# Aliases
MockTeleBot = mock.NonCallableMock
MockMessage = mock.NonCallableMock

class MockResources(mock.NonCallableMock):
    def __init__(self, book=True, **kwargs):
        super().__init__(**kwargs)

        if book is False:
            self.BOOK = None

        self.configure_mock(**{
            'get_events.side_effect': lambda n: self.EVENTS[:n],
            'get_packt_free_book.return_value': self.BOOK,
            'get_short_url.side_effect': lambda url: url,
            'load_states.return_value': defaultdict(
                lambda: defaultdict(
                    lambda: util.StateDict({}, mock.call)
                )
            ),
        })

    EVENTS = [
        {'link': 'https://www.meetup.com/GDG-Aracaju/events/229313880/',
         'name': 'Hackeando sua Carreira #Hangout',
         'time': datetime.fromtimestamp(1459378800, AJU_TZ)},
        {'link': 'https://www.meetup.com/GDG-Aracaju/events/229623381/',
         'name': 'Android Jam 2: Talks Dia 2',
         'time': datetime.fromtimestamp(1459612800, AJU_TZ)},
        {'link': 'https://www.meetup.com/GDG-Aracaju/events/mwnsrlyvgbjb/',
         'name': 'Coding Dojo',
         'time': datetime.fromtimestamp(1459980000, AJU_TZ)},
        {'link': 'https://www.meetup.com/GDG-Aracaju/events/229591464/',
         'name': 'O Caminho para uma Arquitetura Elegante #Hangout',
         'time': datetime.fromtimestamp(1460160000, AJU_TZ)},
        {'link': 'https://www.meetup.com/GDG-Aracaju/events/229770309/',
         'name': 'Android Jam 2: #Curso Dia 2',
         'time': datetime.fromtimestamp(1460217600, AJU_TZ)},
        {'link': 'https://www.meetup.com/GDG-Aracaju/events/mwnsrlyvhbgb/',
         'name': 'Coding Dojo',
         'time': datetime.fromtimestamp(1462399200, AJU_TZ)},
        {'link': 'https://www.meetup.com/GDG-Aracaju/events/229951204/',
         'name': 'Google I/O Extended',
         'time': datetime.fromtimestamp(1463587200, AJU_TZ)},
        {'link': 'https://www.meetup.com/GDG-Aracaju/events/229951264/',
         'name': 'Google IO Extended 2016',
         'time': datetime.fromtimestamp(1463608800, AJU_TZ)},
    ]

    BOOK = util.AttributeDict(
        name="Android 2099",
        summary="Good practices with Miguel O’Hara",
        cover='//test.jpg',
        expires=4091565600,
    )


class TestGDGAjuBot(unittest.TestCase):
    config = util.BotConfig(group_name='Test-Bot')

    # Bot commands tests

    def test_send_welcome(self):
        bot, resources, message = MockTeleBot(), MockResources(), MockMessage()
        g_bot = GDGAjuBot(self.config, bot, resources)
        g_bot.send_welcome(message)
        self._assert_send_welcome(bot, message)

    def test_help(self):
        bot, resources, message = MockTeleBot(), MockResources(), MockMessage()
        g_bot = GDGAjuBot(self.config, bot, resources)
        g_bot.help(message)
        self._assert_help_message(bot, message)

    def test_list_upcoming_events(self):
        bot, resources, message = MockTeleBot(), MockResources(), MockMessage()
        g_bot = GDGAjuBot(self.config, bot, resources)
        g_bot.list_upcoming_events(message)

        # Verifica se o response criado está correto
        self._assert_list_upcoming_events(bot, message)

        # Garante que o cache mutável não gerará uma exceção
        n_calls = len(bot.method_calls)
        g_bot.list_upcoming_events(message)
        assert len(bot.method_calls) > n_calls

    def test_packtpub_free_learning(self):
        bot, resources, message = MockTeleBot(), MockResources(), MockMessage()
        g_bot = GDGAjuBot(self.config, bot, resources)
        ts = resources.BOOK.expires

        # Sem warning
        g_bot.packtpub_free_learning(message, now=datetime.fromtimestamp(ts - 10*3600, tz=AJU_TZ))
        self._assert_packtpub_free_learning(bot, message)

        # Os próximos testes verificam cada um dos warnings
        g_bot.packtpub_free_learning(message, now=datetime.fromtimestamp(ts - 59*60, tz=AJU_TZ))
        self._assert_packtpub_free_learning(bot, message, warning="1 hora")

        g_bot.packtpub_free_learning(message, now=datetime.fromtimestamp(ts - 29*60, tz=AJU_TZ))
        self._assert_packtpub_free_learning(bot, message, warning="meia hora")

        g_bot.packtpub_free_learning(message, now=datetime.fromtimestamp(ts - 9*60, tz=AJU_TZ))
        self._assert_packtpub_free_learning(bot, message, warning="10 minutos")

        g_bot.packtpub_free_learning(message, now=datetime.fromtimestamp(ts - 59, tz=AJU_TZ))
        self._assert_packtpub_free_learning(bot, message, warning="1 minuto")

        g_bot.packtpub_free_learning(message, now=datetime.fromtimestamp(ts - 29, tz=AJU_TZ))
        self._assert_packtpub_free_learning(bot, message, warning="30 segundos")

    def test_book_unavailable(self):
        bot, resources, message = MockTeleBot(), MockResources(book=False), MockMessage()
        g_bot = GDGAjuBot(self.config, bot, resources)

        r = "Parece que não tem um livro grátis hoje 😡\n\n" \
            "Se acha que é um erro meu, veja com seus próprios olhos em https://www.packtpub.com/packt/offers/free-learning"

        g_bot.packtpub_free_learning(message)
        bot.send_message.assert_called_with(message.chat_id, r, parse_mode="Markdown",
                                            disable_web_page_preview=True, reply_to_message_id=message.message_id)

        resources.book = MockResources.BOOK
        g_bot.packtpub_free_learning(message, now=datetime.fromtimestamp(resources.book.expires + 1, tz=AJU_TZ))
        bot.send_message.assert_called_with(message.chat_id, r, parse_mode="Markdown",
                                            disable_web_page_preview=True, reply_to_message_id=message.message_id)

    def test_about(self):
        bot, resources, message = MockTeleBot(), MockResources(), MockMessage(id=0xB00B)
        g_bot = GDGAjuBot(self.config, bot, resources)
        g_bot.about(message)
        self._assert_about(bot, message)

    def _assert_send_welcome(self, bot, message):
        self._assert_mockbot(bot)
        response = bot.reply_to.call_args[0][1]
        assert '/help' in response
        for group in self.config.group_name:
            assert group in response

    def _assert_help_message(self, bot, message):
        self._assert_mockbot(bot)
        commands = ('/help', '/book', '/events', '/about')
        response = bot.reply_to.call_args[0][1]
        for command in commands:
            assert command in response

    def _assert_list_upcoming_events(self, bot, message):
        self._assert_mockbot(bot)
        r = ("[Hackeando sua Carreira #Hangout](http://www.meetup.com/GDG-Aracaju/events/229313880/): 30/03 20h\n"
             "[Android Jam 2: Talks Dia 2](http://www.meetup.com/GDG-Aracaju/events/229623381/): 02/04 13h\n"
             "[Coding Dojo](http://www.meetup.com/GDG-Aracaju/events/mwnsrlyvgbjb/): 06/04 19h\n"
             "[O Caminho para uma Arquitetura Elegante #Hangout](http://www.meetup.com/GDG-Aracaju/events/229591464/): 08/04 21h\n"
             "[Android Jam 2: #Curso Dia 2](http://www.meetup.com/GDG-Aracaju/events/229770309/): 09/04 13h")
        bot.send_message.assert_called_with(message.chat_id, r, parse_mode="Markdown",
                                            disable_web_page_preview=True, reply_to_message_id=message.message_id)

    def _assert_packtpub_free_learning(self, bot, message, warning=''):
        self._assert_mockbot(bot)
        warning = '' if not warning else '⌛️ Menos de %s!' % warning

        r = ("Confira o livro gratuito de hoje da Packt Publishing 🎁\n\n"
             "📖 [Android 2099](https://www.packtpub.com/packt/offers/free-learning)\n"
             "🔎 Good practices with Miguel O’Hara\n") + warning
        kw_call = dict(parse_mode="Markdown", disable_web_page_preview=True)

        bot.send_photo.assert_called_with(message.chat_id, photo='//test.jpg', reply_to_message_id=message.message_id, **kw_call)
        bot.send_message.assert_called_with(message.chat_id, r, **kw_call)

    def _assert_about(self, bot, message):
        self._assert_mockbot(bot)
        link = "https://github.com/GDGAracaju/GDGAjuBot/"
        response = bot.send_message.call_args[0][1]
        assert link in response

    def _assert_mockbot(self, bot):
        assert isinstance(bot, MockTeleBot)

    # Internals tests

    def test_smart_reply(self):
        bot, resources = MockTeleBot(), MockResources()
        message = MockMessage(id=0x6D6)
        g_bot = GDGAjuBot(self.config, bot, resources)
        text = "I <3 GDG Aracaju"

        # Mensagens privadas não fazem link
        message.chat.type = "private"
        g_bot._send_smart_reply(message, text)
        bot.send_message.assert_called_with(message.chat_id, text, reply_to_message_id=message.message_id)
        g_bot._send_smart_reply(message, text)
        bot.send_message.assert_called_with(message.chat_id, text, reply_to_message_id=message.message_id)

        # Configurando MockTeleBot.send_message() para retornar um MockMessage com um message_id
        bot.send_message.return_value = MockMessage(message_id=82)

        # Mensagens de grupo fazem link
        message.chat.type = "group"
        g_bot._send_smart_reply(message, text)
        bot.send_message.assert_called_with(message.chat_id, text, reply_to_message_id=message.message_id)
        g_bot._send_smart_reply(message, text)
        bot.send_message.assert_called_with(message.chat.id, mock.ANY,
                                            reply_to_message_id=82)
        the_answer = bot.send_message.call_args[0][1]
        assert the_answer[2:] in ALREADY_ANSWERED_TEXTS


class TestResources(unittest.TestCase):
    cd = os.path.dirname(__file__)

    def test_extract_packt_free_book(self):
        content = open(os.path.join(self.cd, 'packtpub-free-learning.html.fixture'), 'rb')
        result = {'name': "Oracle Enterprise Manager 12c Administration Cookbook",
                  'summary': "Over 50 practical recipes to install, configure, and monitor your Oracle setup using Oracle Enterprise Manager",
                  'cover': "https://d1ldz4te4covpm.cloudfront.net/sites/default/files/imagecache/dotd_main_image/7409EN.jpg",
                  'expires': 1459378800}

        assert bot.Resources.extract_packt_free_book(content) == result
