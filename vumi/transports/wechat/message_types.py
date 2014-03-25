import re
import json
from xml.etree.ElementTree import Element, SubElement, tostring

from vumi.transports.wechat.errors import WeChatException


def get_children(node, name):
    return node.getElementsByTagName(name)


def get_child(node, name):
    [child] = get_children(node, name)
    return child


def get_child_value(node, name, default=None):
    try:
        child = get_child(node, name)
        return ''.join([grandchild.value for grandchild in child.childNodes])
    except ValueError:
        return default


def append(node, tag, value):
    el = SubElement(node, tag)
    el.text = value


class TextMessage(object):

    def __init__(self, to_user_name, from_user_name, create_time, content,
                 msg_id=None):
        self.to_user_name = to_user_name
        self.from_user_name = from_user_name
        self.create_time = create_time
        self.content = content
        self.msg_id = msg_id

    @classmethod
    def from_xml(cls, doc):
        root = doc.firstChild()
        return cls(*[get_child_value(root, name)
                     for name in ['ToUserName',
                                  'FromUserName',
                                  'CreateTime',
                                  'Content',
                                  'MsgId']])

    @classmethod
    def from_vumi_message(cls, message):
        md = message['transport_metadata'].get('wechat', {})
        from_addr = md.get('ToUserName') or message['from_addr']
        return cls(message['to_addr'], from_addr,
                   message['timestamp'].strftime('%s'),
                   message['content'])

    def to_xml(self):
        xml = Element('xml')
        append(xml, 'ToUserName', self.to_user_name)
        append(xml, 'FromUserName', self.from_user_name)
        append(xml, 'CreateTime', self.create_time)
        append(xml, 'MsgType', 'text')
        append(xml, 'Content', self.content)
        return tostring(xml)

    def to_json(self):
        return json.dumps({
            'touser': self.to_user_name,
            'msgtype': 'text',
            'text': {
                'content': self.content,
            }
        })


class NewsMessage(object):

    # Has something URL-ish in it
    URLISH = re.compile(
        r'(?P<before>.*)'
        r'(?P<schema>[a-zA-Z]{4,5})\://'
        r'(?P<domain>[^\s]+)'
        r'(?P<after>.*)')

    def __init__(self, to_user_name, from_user_name, create_time,
                 items=None):
        self.to_user_name = to_user_name
        self.from_user_name = from_user_name
        self.create_time = create_time
        self.items = ([] if items is None else items)

    @classmethod
    def accepts(cls, vumi_message):
        return cls.URLISH.match(vumi_message['content'])

    @classmethod
    def from_vumi_message(cls, match, vumi_message):
        url_data = match.groupdict()
        return cls(
            vumi_message['to_addr'],
            vumi_message['from_addr'],
            vumi_message['timestamp'].strftime('%s'),
            [{
                'url': '%(schema)s://%(domain)s' % url_data,
                'description': '%(before)s%(after)s' % url_data,
            }])

    def to_xml(self):
        xml = Element('xml')
        append(xml, 'ToUserName', self.to_user_name)
        append(xml, 'FromUserName', self.from_user_name)
        append(xml, 'CreateTime', self.create_time)
        append(xml, 'MsgType', 'news')
        append(xml, 'ArticleCount', str(len(self.items)))
        articles = SubElement(xml, 'Articles')
        for item in self.items:
            if not any(item.values()):
                raise WeChatException(
                    'News items must have some values.')

            item_element = SubElement(articles, 'item')
            if 'title' in item:
                append(item_element, 'Title', item['title'])
            if 'description' in item:
                append(item_element, 'Description', item['description'])
            if 'picurl' in item:
                append(item_element, 'PicUrl', item['picurl'])
            if 'url' in item:
                append(item_element, 'Url', item['url'])
        return tostring(xml)

    def to_json(self):
        return json.dumps({
            'touser': self.to_user_name,
            'msgtype': 'news',
            'news': {
                'articles': self.items
            }
        })


class EventMessage(object):

    def __init__(self, to_user_name, from_user_name, create_time, event,
                 event_key=None):
        self.to_user_name = to_user_name
        self.from_user_name = from_user_name
        self.create_time = create_time
        self.event = event
        self.event_key = event_key

    @classmethod
    def from_xml(cls, doc):
        root = doc.firstChild()
        return cls(*[get_child_value(root, name)
                     for name in ['ToUserName',
                                  'FromUserName',
                                  'CreateTime',
                                  'Event',
                                  'EventKey']])
