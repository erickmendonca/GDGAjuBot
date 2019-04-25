import datetime
import json
import logging
from typing import Dict

import requests
import requests.exceptions

from beaker.cache import CacheManager
from beaker.util import parse_cache_config_options
from bs4 import BeautifulSoup

from gdgajubot import util
from gdgajubot.data.database import db, orm, Message, User, Choice, ChoiceConverter, State
from gdgajubot.util import StateDict, MissingDict


def json_encode(info):
    return JSONCodec().encode(info)


def json_decode(info):
    return JSONCodec().decode(info)


class Resources:
    BOOK_URL = "https://www.packtpub.com/packt/offers/free-learning"
    BOOK_API_URL = "https://services.packtpub.com/free-learning-v1/offers"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/51.0.2704.79 Safari/537.36"
    }

    # Configuring cache
    cache = CacheManager(
        **parse_cache_config_options({'cache.type': 'memory'}))

    def __init__(self, config):
        self.config = config
        self.db = self.__initialize_database(**config.database)

        # create delegate method based on choice
        if 'meetup' in config.events_source:
            self.generate_events = self.meetup_events
        else:
            self.generate_events = self.facebook_events

    def __initialize_database(self, **config):
        db.bind(**config)
        db.provider.converter_classes.append((Choice, ChoiceConverter))
        db.generate_mapping(create_tables=True)
        return db

    @cache.cache('get_events', expire=60)
    def get_events(self, list_size=5):
        return list(self.generate_events(list_size))

    def meetup_events(self, n):
        """Obtém eventos do Meetup."""
        # api v3 base url
        all_events = []
        for group in self.config.group_name:
            url = "https://api.meetup.com/{group}/events".format(
                group=group
            )

            # response for the events
            r = requests.get(url, params={
                'key': self.config.meetup_key,
                'status': 'upcoming',
                'only': 'name,time,link',  # filter response to these fields
                'page': n,                 # limit to n events
            })

            # API output
            events = r.json()

            for event in events:
                # convert time returned by Meetup API
                event['time'] = datetime.datetime.fromtimestamp(
                    event['time'] / 1000, tz=util.AJU_TZ)
                # shorten url!
                event['link'] = self.get_short_url(event['link'])

            all_events.extend(events)
        return sorted(all_events, key=lambda x: x['time'])

    def facebook_events(self, n):
        """Obtém eventos do Facebook."""
        all_events = []
        for group in self.config.group_name:
            # api v2.8 base url
            url = "https://graph.facebook.com/v2.8/%s/events" % group

            # response for the events
            r = requests.get(url, params={
                'access_token': self.config.facebook_key,
                'since': 'today',
                'fields': 'name,start_time',  # filter response to these fields
                'limit': n,                   # limit to n events
            })

            # API output
            events = r.json().get('data', [])

            for event in events:
                # convert time returned by Facebook API
                event['time'] = datetime.datetime.strptime(
                    event.pop('start_time'), "%Y-%m-%dT%H:%M:%S%z")
                # create event link
                link = "https://www.facebook.com/events/%s" % event.pop('id')
                # shorten url!
                event['link'] = self.get_short_url(link)
            all_events.extend(events)

        return sorted(all_events, key=lambda x: x['time'])

    @cache.cache('get_packt_free_book', expire=600)
    def get_packt_free_book(self):
        today = datetime.datetime.utcnow().date()
        today_iso = today.isoformat()
        tomorrow_iso = (today + datetime.timedelta(days=1)).isoformat()

        free_book_info = requests.get(
            self.BOOK_API_URL,
            params={
                'dateFrom': today_iso,
                'dateTo': tomorrow_iso,
            }
        ).json()

        free_book_id = free_book_info['data'][0]['productId']
        product_summary = requests.get(
            'https://static.packt-cdn.com/products/{product_id}/summary'.format(
                product_id=free_book_id,
            )
        ).json()

        return {
            'name': product_summary['title'],
            'summary': product_summary['oneLiner'],
            'expires': product_summary['length'],
            'cover': product_summary['coverImage'],
        }

    @cache.cache('get_short_url')
    def get_short_url(self, long_url):
        # Faz a requisição da URL curta somente se houver uma key configurada
        if self.config.url_shortener_key:
            r = requests.post(
                "https://www.googleapis.com/urlshortener/v1/url",
                params={
                    'key': self.config.url_shortener_key,
                    'fields': 'id'
                },
                json={'longUrl': long_url}
            )
            if r.status_code == 200:
                return r.json()['id']
            else:
                logging.exception(r.text)

        # Caso tenha havido algum problema usa a própria URL longa
        return long_url

    ChatState = dict

    @orm.db_session
    def set_state(self, state_id: str, chat_id: int, chat_state: ChatState):
        # to not dump memory-only state
        chat_state = chat_state.copy()
        chat_state.pop('__memory__', None)

        try:
            state = State[chat_id, state_id]
            info = json_decode(state.info)
            info.update(chat_state)
            state.info = json_encode(info)
        except orm.ObjectNotFound:
            State(telegram_id=chat_id, description=state_id, info=json_encode(chat_state))

    @orm.db_session
    def get_state(self, state_id: str, chat_id: int) -> ChatState:
        state = State.get(telegram_id=chat_id, description=state_id)
        if state:
            return json_decode(state.info)
        return {}

    @orm.db_session
    def update_states(self, states: Dict[str, Dict[int, ChatState]]):
        for state_id, data in states.items():
            for chat_id, chat_state in data.items():
                self.set_state(state_id, chat_id, chat_state)

    @orm.db_session
    def load_states(self) -> Dict[str, Dict[int, ChatState]]:
        states = MissingDict(
            lambda state_id: MissingDict(
                lambda chat_id: self.__state_dict(state_id, chat_id,
                                                  self.get_state(state_id, chat_id))
            )
        )

        for state in State.select():
            state_id, chat_id, info = state.description, state.telegram_id, state.info
            states[state_id][chat_id] = self.__state_dict(state_id, chat_id, json_decode(info))

        return states

    def __state_dict(self, state_id, chat_id, data):
        # reserve a memory-only key
        if '__memory__' not in data:
            data['__memory__'] = {}

        return StateDict(
            data, dump_function=lambda state: self.set_state(state_id, chat_id, state)
        )

    @orm.db_session
    def log_message(self, message, *args, **kwargs):
        try:
            user = User[message.from_user.id]
        except orm.ObjectNotFound:
            user = User(
                telegram_id=message.from_user.id,
                telegram_username=message.from_user.name,
            )
        message = Message(
            sent_by=user, text=message.text, sent_at=message.date,
        )
        print(
            'Logging message: {}'.format(message),
        )

    @orm.db_session
    def list_all_users(self):
        users = User.select().order_by(User.telegram_username)[:]
        return tuple(users)

    @orm.db_session
    def is_user_admin(self, user_id):
        try:
            user = User[user_id]
        except orm.ObjectNotFound:
            return False
        return user.is_bot_admin


DATETIME_FORMAT = '%Y-%m-%dT%H:%M:%S.%f%z'


class JSONCodec:
    class Encoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, datetime.datetime):
                return {'__datetime__': obj.strftime(DATETIME_FORMAT)}
            return super().default(obj)

    class Decoder(json.JSONDecoder):
        def __init__(self):
            super().__init__(object_hook=self.object_hook)

        @staticmethod
        def object_hook(obj):
            if '__datetime__' in obj:
                return datetime.datetime.strptime(obj['__datetime__'], DATETIME_FORMAT)
            return obj

    # singleton
    def __new__(cls, **kwargs):
        if not hasattr(cls, 'instance'):
            cls.instance = super().__new__(cls)
            cls.instance.encode = cls.Encoder().encode
            cls.instance.decode = cls.Decoder().decode
        return cls.instance
