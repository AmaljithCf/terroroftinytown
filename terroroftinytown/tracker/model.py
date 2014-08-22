# encoding=utf-8
import base64
import calendar
import contextlib
import datetime
import hmac
import json
import os

from sqlalchemy import func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.orm.session import make_transient
from sqlalchemy.sql.expression import insert, update
from sqlalchemy.sql.schema import Column, ForeignKey
from sqlalchemy.sql.sqltypes import String, Binary, Float, Boolean, Integer, \
    DateTime
from sqlalchemy.sql.type_api import TypeDecorator

from terroroftinytown.tracker.errors import NoItemAvailable


Base = declarative_base()
Session = sessionmaker()


@contextlib.contextmanager
def new_session():
    session = Session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()


class JsonType(TypeDecorator):
    impl = String

    def process_bind_param(self, value, engine):
        return json.dumps(value)

    def process_result_value(self, value, engine):
        if value:
            return json.loads(value)
        else:
            return None


class User(Base):
    '''User accounts that manager the tracker.'''
    __tablename__ = 'users'

    username = Column(String, primary_key=True)
    salt = Column(Binary, nullable=False)
    hash = Column(Binary, nullable=False)

    def set_password(self, password):
        self.salt = new_salt()
        self.hash = make_hash(password, self.salt)

    def check_password(self, password):
        test_hash = make_hash(password, self.salt)

        if len(self.hash) != len(test_hash):
            return False

        iterable = [a == b for a, b in zip(self.hash, test_hash)]
        ok = True

        for result in iterable:
            ok &= result

        return ok

    @classmethod
    def no_users_exist(cls):
        with new_session() as session:
            user = session.query(User).first()

            return user is None

    @classmethod
    def is_user_exists(cls, username):
        with new_session() as session:
            user = session.query(User).filter_by(username=username).first()

            return user is not None

    @classmethod
    def all_usernames(cls):
        with new_session() as session:
            users = session.query(User.username)

            return list([user.username for user in users])

    @classmethod
    def save_new_user(cls, username, password):
        with new_session() as session:
            user = User(username=username)
            user.set_password(password)
            session.add(user)

    @classmethod
    def check_account(cls, username, password):
        with new_session() as session:
            user = session.query(User).filter_by(username=username).first()
            return user.check_password(password)

    @classmethod
    def update_password(cls, username, password):
        with new_session() as session:
            user = session.query(User).filter_by(username=username).first()
            user.set_password(password)

    @classmethod
    def delete_user(cls, username):
        with new_session() as session:
            session.query(User).filter_by(username=username).delete()


class Project(Base):
    '''Project settings.'''
    __tablename__ = 'projects'

    name = Column(String, primary_key=True)
    min_version = Column(String)
    alphabet = Column(String, default='0123456789abcdefghijklmnopqrstuvwxyz'
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    url_template = Column(String, default='http://example.com/{shortcode}')
    request_delay = Column(Float, default=0.5)
    redirect_codes = Column(JsonType, default=[301, 302, 303, 307])
    no_redirect_codes = Column(JsonType, default=[404])
    unavailable_codes = Column(JsonType, default=[200])
    banned_codes = Column(JsonType, default=[420])
    body_regex = Column(String)
    custom_code_required = Column(Boolean)
    method = Column(String, default='head')

    enabled = Column(Boolean, default=True)
    autoqueue = Column(Boolean)
    num_count_per_item = Column(Integer, default=50, nullable=False)
    max_num_items = Column(Integer, default=1000, nullable=False)
    lower_sequence_num = Column(Integer, default=0, nullable=False)
    autorelease_time = Column(Integer, default=3600 * 6)

    def to_dict(self):
        return {
            'name': self.name,
            'min_version': self.min_version,
            'alphabet': self.alphabet,
            'url_template': self.url_template,
            'request_delay': self.request_delay,
            'redirect_codes': self.redirect_codes,
            'no_redirect_codes': self.no_redirect_codes,
            'unavailable_codes': self.unavailable_codes,
            'banned_codes': self.banned_codes,
            'body_regex': self.body_regex,
            'custom_code_required': self.custom_code_required,
            'method': self.method,
        }

    @classmethod
    def all_project_names(cls):
        with new_session() as session:
            projects = session.query(Project.name)

            return list([project.name for project in projects])

    @classmethod
    def new_project(cls, name):
        with new_session() as session:
            project = Project(name=name)
            session.add(project)

    @classmethod
    def get_plain(cls, name):
        with new_session() as session:
            project = session.query(Project).filter_by(name=name).first()

            make_transient(project)
            return project

    @classmethod
    @contextlib.contextmanager
    def get_session_object(cls, name):
        with new_session() as session:
            project = session.query(Project).filter_by(name=name).first()
            yield project

    @classmethod
    def delete_project(cls, name):
        # FIXME: need to cascade the deletes
        with new_session() as session:
            session.query(Project).filter_by(name=name).delete()


class Item(Base):
    __tablename__ = 'items'
    id = Column(Integer, primary_key=True)

    project_id = Column(Integer, ForeignKey('projects.name'), nullable=False)
    project = relationship('Project')

    lower_sequence_num = Column(Integer, nullable=False)
    upper_sequence_num = Column(Integer, nullable=False)
    datetime_claimed = Column(DateTime)
    tamper_key = Column(String)
    username = Column(String)
    ip_address = Column(String)

    def to_dict(self):
        return {
            'id': self.id,
            'project': self.project.to_dict(),
            'lower_sequence_num': self.lower_sequence_num,
            'upper_sequence_num': self.upper_sequence_num,
            'datetime_claimed': calendar.timegm(self.datetime_claimed.utctimetuple()) if self.datetime_claimed else None,
            'tamper_key': self.tamper_key,
            'username': self.username,
            'ip_address': self.ip_address,
        }

    @classmethod
    def get_items(cls, project_name):
        with new_session() as session:
            rows = session.query(Item).filter_by(project_id=project_name).order_by(Item.datetime_claimed)

            return list([item.to_dict() for item in rows])

    @classmethod
    def add_items(cls, project_name, sequence_list):
        with new_session() as session:
            query = insert(Item)
            query_args = []

            for lower_num, upper_num in sequence_list:
                query_args.append({
                    'project_id': project_name,
                    'lower_sequence_num': lower_num,
                    'upper_sequence_num': upper_num,
                })

            session.execute(query, query_args)

    @classmethod
    def delete(cls, item_id):
        with new_session() as session:
            session.query(Item).filter_by(id=item_id).delete()

    @classmethod
    def release(cls, item_id):
        with new_session() as session:
            item = session.query(Item).filter_by(id=item_id).first()
            item.datetime_claimed = None
            item.ip_address = None
            item.username = None

    @classmethod
    def release_all(cls, project_name, old_date):
        with new_session() as session:
            session.query(Item).filter_by(project_id=project_name)\
                .filter(Item.datetime_claimed <= old_date).update({
                    'datetime_claimed': None,
                    'ip_address': None,
                    'username': None,
                })

    @classmethod
    def delete_all(cls, project_name):
        with new_session() as session:
            session.query(Item).filter_by(project_id=project_name).delete()


class BlockedUser(Base):
    '''Blocked IP addresses or usernames.'''
    __tablename__ = 'blocked_users'

    username = Column(String, primary_key=True)
    note = Column(String)

    @classmethod
    def block_username(cls, username, note=None):
        with new_session() as session:
            session.add(BlockedUser(username=username, note=note))

    @classmethod
    def unblock_username(cls, username):
        with new_session() as session:
            session.query(BlockedUser).filter_by(username=username).delete()

    @classmethod
    def is_username_blocked(cls, username):
        with new_session() as session:
            result = session.query(BlockedUser).filter_by(username=username).first()
            if result:
                return True

    @classmethod
    def all_blocked_usernames(cls):
        with new_session() as session:
            names = session.query(BlockedUser.username)

            return list(names)


class Result(Base):
    '''Unshortend URL.'''
    __tablename__ = 'results'

    id = Column(Integer, primary_key=True)

    project_id = Column(Integer, ForeignKey('projects.name'), nullable=False)
    project = relationship('Project')

    shortcode = Column(String, nullable=False)
    url = Column(String, nullable=False)
    encoding = Column(String, nullable=False)
    datetime = Column(DateTime)


def make_hash(plaintext, salt):
    key = salt
    msg = plaintext.encode('ascii')

    return hmac.new(key, msg).digest()


def new_salt():
    return os.urandom(16)


def new_tamper_key():
    return base64.b16encode(os.urandom(16)).decode('ascii')

def generate_item(session):
    num_queue = session.query(
        Item.project_id,
        func.count(Item.id).label('queue_size')
    ) \
        .group_by(Item.project_id) \
        .subquery()

    # TODO: Check for client minimum version
    query = session.query(Project, num_queue.c.queue_size) \
        .filter_by(enabled=True, autoqueue=True) \
        .outerjoin(num_queue, Project.name == num_queue.c.project_id) \
        .filter(func.coalesce(num_queue.c.queue_size, 0) < Project.max_num_items) \
        .first()
    # XXX: Should the project be randomized?

    if not query:
        raise NoItemAvailable()

    project, queue_size = query

    item_count = project.num_count_per_item
    upper_sequence_num = project.lower_sequence_num + item_count - 1

    item = Item(
        project=project,
        lower_sequence_num=project.lower_sequence_num,
        upper_sequence_num=upper_sequence_num,
    )

    project.lower_sequence_num = upper_sequence_num + 1

    session.add(item)
    return item

def checkout_item(username, ip_address):
    with new_session() as session:
        item = session.query(Item) \
            .filter_by(username=None) \
            .join(Item.project) \
            .filter_by(enabled=True).first()

        if not item:
            item = generate_item(session)

        item.datetime_claimed = datetime.datetime.utcnow()
        item.tamper_key = new_tamper_key()
        item.username = username
        item.ip_address = ip_address

        # Item should be committed now to generate ID for
        # newly generated items        
        session.commit()

        return item.to_dict()


def checkin_item(item_id, tamper_key, results):
    with new_session() as session:
        item = session.query(Item).filter_by(id=item_id, tamper_key=tamper_key).first()

        query_args = []
        time = datetime.datetime.now()

        for shortcode in results.keys():
            url = results[shortcode]['url']
            encoding = results[shortcode]['encoding']
            query_args.append({
                'project_id': item.project_id,
                'shortcode': shortcode,
                'url': url,
                'encoding': encoding,
                'datetime': time
            })

        if len(query_args) > 0:
            query = insert(Result)
            session.execute(query, query_args)

        session.delete(item)
