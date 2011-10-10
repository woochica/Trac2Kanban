# encoding: utf-8

"""A Trac plugin to create kanban cards from tickets on team's board.

It uses the LeanKitKanban service."""

from genshi.builder import tag
from genshi.filters import Transformer

import httplib2
import re
import simplejson

from trac.core import *
from trac.env import Environment
from trac.ticket.model import Ticket
from trac.web import IRequestHandler
from trac.web.api import ITemplateStreamFilter


CONFIG_SECTION = 'trac2kanban'


class Trac2KanbanPlugin(Component):
    """Provides a "Kanbanize!" button on the ticket page to copy
    ticket data to Kanban board."""
    implements(IRequestHandler, ITemplateStreamFilter)

    LABEL_NEW = 'Kanbanize!'
    LABEL_EXISTING = 'Baby on Board'
    ROUTE_PATTERN = '^%s/(?P<ticket_id>\d+)$'

    def filter_stream(self, req, method, filename, stream, data):
        """Adds button to ticket page."""
        if filename != 'ticket.html':
            return stream
        ticket = data.get('ticket')
        permission = self.config.get(CONFIG_SECTION, 'permission')
        if not ticket or not ticket.exists or permission not in req.perm(ticket.resource):
            return stream
        url = self.config.get(CONFIG_SECTION, 'kanban_base_url')
        service = LeanKitService(url, self.env)
        board = service.get_board(ticket['team'])
        if not board:
            return stream
        html = Transformer('//div[@class="description"]')
        return stream | html.after(self._kanban_form(board, ticket))

    def _kanban_form(self, board, ticket):
        u"""Returns HTML markup for button."""
        url = board.get_card_url(ticket.id)
        if url:
            label = self.LABEL_EXISTING
        else:
            label = self.LABEL_NEW
            path = self.config.get(CONFIG_SECTION, 'trac_path_kanban')
            url = "%s/%d" % (path, ticket.id)
        return tag.a(label, href=url)

    def match_request(self, req):
        u"""Returns true if request path matches plugin pattern."""
        return self._parse_ticket_id(req)

    def process_request(self, req):
        """Creates Kanban card to related team's board from ticket."""
        ticket_id = self._parse_ticket_id(req)
        ticket = self._get_ticket(ticket_id)
        url = self.config.get(CONFIG_SECTION, 'kanban_base_url')
        service = LeanKitService(url, self.env)
        board = service.get_board(ticket['team'])
        card = service.create_card(ticket)
        board.add_card(card)
        path = self.config.get(CONFIG_SECTION, 'trac_path_ticket')
        self._redirect_to(req, "%s/%d" % (path, ticket.id))

    def _parse_ticket_id(self, req):
        """Returns ticket ID by HTTP request."""
        path = self.config.get(CONFIG_SECTION, 'trac_path_kanban')
        matches = re.match(self.ROUTE_PATTERN % path, req.path_info)
        if matches:
            ticket_id, = matches.groups()
            return ticket_id
        return None

    def _get_ticket(self, ticket_id):
        """Returns `Ticket` instance by HTTP request."""
        ticket = Ticket(self.env, ticket_id)
        if not ticket or not ticket.exists:
            return None
        return ticket

    def _redirect_to(self, req, url):
        """Redirects client to URL."""
        req.send_response(302)
        req.send_header('Location', url)
        req.send_header('Content-Length', 0)
        req.end_headers()
        req.write('')


class Board(object):
    """Represents a Kanban board."""

    def __init__(self, service, board_name, trac_env):
        self.service = service
        self.env = trac_env
        boards = dict(zip(self.env.config.getlist(CONFIG_SECTION, 'trac_teams'),
                          self.env.config.getlist(CONFIG_SECTION, 'kanban_boards')))
        self.board_id = boards[board_name]
        self.lane_id, self.card_type_id = self._get_info()

    def _get_info(self):
        """Returns board info."""
        board_info_url = "%s/Kanban/Api/Board/%s/GetBoardIdentifiers" % \
            (self.service.base_url, self.board_id)
        content = self.service.call(board_info_url)
        return self._parse_info(content)

    def _parse_info(self, data):
        """Returns list of lane ID and card type ID."""
        lane_position = int(self.env.config.get(CONFIG_SECTION, 'kanban_lane_position'))
        card_types = self.env.config.getlist(CONFIG_SECTION, 'kanban_card_type')
        info = [data['ReplyData'][0]['Lanes'][lane_position]['Id']]
        info.extend(card[u'Id'] for card in data['ReplyData'][0]['CardTypes'] if card[u'Name'] in card_types)
        return info

    def add_card(self, card, position=0):
        """Add card to kanban board."""
        card["TypeId"] = self.card_type_id
        add_card_url = "%s/Kanban/Api/Board/%s/AddCard/Lane/%s/Position/%s" % \
            (self.service.base_url, self.board_id, self.lane_id, position)
        card_json = simplejson.dumps(card)
        self.service.call(add_card_url, method="POST", data=card_json,
                          headers={'Content-Type': 'application/json',
                                   'Content-Length': str(len(card_json))})

    def get_card_url(self, external_id):
        """Returns URL of card with external ID."""
        get_card_url = '%s/Kanban/Api/Board/%s/GetCardByExternalId/%s' % \
            (self.service.base_url, self.board_id, external_id)
        content = self.service.call(get_card_url, method="GET")
        if content["ReplyCode"] == 200:
            return "%s/Boards/View/%s" % (self.service.base_url, self.board_id)
        return None


class LeanKitService(object):
    """A LeanKitKanBan service."""

    def __init__(self, base_url, trac_env):
        self.base_url = base_url
        self.env = trac_env

    def get_board(self, board_name):
        """Returns `Board` instance."""
        boards = dict(zip(self.env.config.getlist(CONFIG_SECTION, 'trac_teams'),
                          self.env.config.getlist(CONFIG_SECTION, 'kanban_boards')))
        if board_name not in boards:
            return None
        return Board(self, board_name, self.env)

    def create_card(self, ticket):
        """Creates kanban card from ticket."""
        ticket_path = self.env.config.get(CONFIG_SECTION, 'trac_path_ticket')
        trac_url = self.env.config.get('trac', 'base_url')
        priorities = dict(zip(self.env.config.getlist(CONFIG_SECTION, 'trac_priorities'),
                              self.env.config.getlist(CONFIG_SECTION, 'kanban_priorities')))
        priority_field = self.env.config.get(CONFIG_SECTION, 'trac_priority_field')
        return {
            "Title": ticket['summary'],
            "Description": ticket['description'],
            "TypeId": None,
            "Priority": priorities[ticket[priority_field]],
            "Size": 1,
            "IsBlocked": False,
            "BlockReason": "",  # must specify if Card is blocked
            "DueDate": None, # dd/MM/yyyy
            "ExternalSystemName": "Trac",
            "ExternalSystemUrl": '%s/%s/%d' % (trac_url, ticket_path, ticket.id),
            "Tags": '', # comma separated list of strings
            "ClassOfServiceId": None,
            "ExternalCardID": ticket.id,
            "AssignedUserIds": [] # array of Ids for each board user to assign, get from GetBoardIdentifiers
            }

    def call(self, url, method="GET", data=None, headers=None):
        """Calls service API method."""
        http = httplib2.Http()
        user = self.env.config.get(CONFIG_SECTION, 'kanban_auth_user')
        password = self.env.config.get(CONFIG_SECTION, 'kanban_auth_password')
        http.add_credentials(user, password)
        self.env.log.debug('Calling API method:')
        self.env.log.debug('  url = %r' % url)
        self.env.log.debug('  method = %r' % method)
        self.env.log.debug('  headers = %r' % headers)
        self.env.log.debug('  data = %r' % data)
        resp, json = http.request(url, method=method, headers=headers, body=data)
        if resp['status'] == "401":
            self.env.log.debug("Unauthorized: Access is denied due to invalid credentials.")
        elif resp['status'] == "200":
            self.env.log.debug("Response OK: %r\n" % resp)
            self.env.log.debug("Raw content: %r\n" % json)
        content = simplejson.loads(json)
        return content
