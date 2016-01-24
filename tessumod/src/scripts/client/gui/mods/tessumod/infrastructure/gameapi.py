# TessuMod: Mod for integrating TeamSpeak into World of Tanks
# Copyright (C) 2015  Janne Hakonen
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA

import BigWorld
from gui import SystemMessages
from gui.shared.notifications import NotificationGuiSettings
from messenger.proto.shared_find_criteria import FriendsFindCriteria
from messenger.storage import storage_getter
from notification import NotificationMVC
from notification.settings import NOTIFICATION_TYPE
from notification.decorators import _NotificationDecorator
from debug_utils import _doLog, _makeMsgHeader
import Event as _Event
import VOIP
from VOIP.VOIPManager import VOIPManager
import BattleReplay

from functools import partial
from traceback import format_exception
import sys

from utils import patch_instance_method
import log

Event = _Event.Event

class Logger(object):

	@staticmethod
	def debug(msg, *args):
		if log.CURRENT_LOG_LEVEL <= log.LOG_LEVEL.DEBUG:
			_doLog('DEBUG', log.prefix_with_timestamp(msg), args)

	@staticmethod
	def note(msg, *args):
		if log.CURRENT_LOG_LEVEL <= log.LOG_LEVEL.NOTE:
			_doLog('NOTE', log.prefix_with_timestamp(msg), args)

	@staticmethod
	def warning(msg, *args):
		if log.CURRENT_LOG_LEVEL <= log.LOG_LEVEL.WARNING:
			_doLog('WARNING', log.prefix_with_timestamp(msg), args)

	@staticmethod
	def error(msg, *args):
		if log.CURRENT_LOG_LEVEL <= log.LOG_LEVEL.ERROR:
			_doLog('ERROR', log.prefix_with_timestamp(msg), args)

	@staticmethod
	def exception():
		msg = _makeMsgHeader(sys._getframe(1)) + "\n"
		etype, value, tb = sys.exc_info()
		msg += "".join(format_exception(etype, value, tb, None))
		BigWorld.logError('EXCEPTION', log.prefix_with_timestamp(msg), None)

class EventLoop(object):

	@classmethod
	def callback(cls, timeout, function, *args, **kwargs):
		if args or kwargs:
			function = partial(function, *args, **kwargs)
		return BigWorld.callback(timeout, function)

	@classmethod
	def cancel_callback(cls, id):
		BigWorld.cancelCallback(id)

	@classmethod
	def create_callback_repeater(cls, function):
		return _EventRepeater(function)

class _EventRepeater(object):

	def __init__(self, function):
		self.__id       = None
		self.__timeout  = None
		self.__function = function

	def start(self, timeout):
		self.__timeout = timeout
		self.stop()
		self.__start_callback()

	def __start_callback(self):
		if self.__id is None:
			self.__id = EventLoop.callback(self.__timeout, self.__on_timeout)

	def stop(self):
		if self.__id is not None:
			EventLoop.cancel_callback(self.__id)
			self.__id = None

	def __on_timeout(self):
		self.__id = None
		self.__function()
		self.__start_callback()

class Battle(object):

	@classmethod
	def get_camera_position(cls):
		camera = BigWorld.camera()
		if camera:
			return (camera.position.x, camera.position.y, camera.position.z)

	@classmethod
	def get_camera_direction(cls):
		camera = BigWorld.camera()
		if camera:
			return (camera.direction.x, camera.direction.y, camera.direction.z)

	@classmethod
	def get_entity(cls, entity_id):
		entity = BigWorld.entities.get(entity_id)
		if entity:
			assert hasattr(entity, "position")
			return entity

	@classmethod
	def patch_battle_replay_play(cls, patch_function):
		original_method = BattleReplay.BattleReplay.play
		def wrapper_play(self, fileName=None):
			return patch_function(self, original_method, fileName)
		BattleReplay.BattleReplay.play = wrapper_play

	@classmethod
	def find_vehicle_id(cls, matcher):
		'''Finds 'vehicle_id' using given 'matcher' function.'''
		try:
			vehicles = BigWorld.player().arena.vehicles
			for id in vehicles:
				vehicle = vehicles[id]
				if matcher(vehicle):
					return id
		except AttributeError:
			pass
		return None

	@classmethod
	def get_vehicle(cls, vehicle_id):
		'''Returns vehicle info with matching 'vehicle_id' if available.
		Returns None if not.
		'''
		if vehicle_id is None:
			return None
		try:
			return BigWorld.player().arena.vehicles[vehicle_id]
		except AttributeError:
			pass

class Player(object):

	@classmethod
	def get_player_by_dbid(cls, dbid):
		'''Extracts player information with matching account 'dbid' from
		various locations.
		''' 
		vehicle_id = Battle.find_vehicle_id(lambda v: v["accountDBID"] == dbid)
		if vehicle_id is not None:
			vehicle = Battle.get_vehicle(vehicle_id)
			return dict(id=dbid, name=vehicle["name"], vehicle_id=vehicle_id, is_alive=vehicle["isAlive"])
		info = cls.find_prebattle_account_info(lambda i: i["dbID"] == dbid)
		if info:
			return dict(id=dbid, name=info["name"])
		return None

	@classmethod
	def get_my_name(cls):
		'''Returns current player's nickname. None if not available.'''
		try:
			return BigWorld.player().name
		except AttributeError:
			pass

	@classmethod
	def get_my_dbid(cls):
		'''Returns current player's account dbid. None if not available.'''
		try:
			return BigWorld.player().databaseID
		except AttributeError:
			try:
				return Battle.get_vehicle(BigWorld.player().playerVehicleID)["accountDBID"]
			except AttributeError:
				pass

	@classmethod
	def find_prebattle_account_info(cls, matcher):
		'''Finds player information from prebattle rosters (e.g. in practise room
		when you assign players to teams). Given 'matcher' function is used pick
		desired info. Returns None if nothing found.
		'''
		try:
			rosters = BigWorld.player().prebattle.rosters
			for roster in rosters:
				for id in rosters[roster]:
					info = rosters[roster][id]
					if matcher(info):
						return info
		except AttributeError:
			pass

	@classmethod
	def get_players(cls, in_battle=False, in_prebattle=False, clanmembers=False, friends=False):
		if in_battle:
			try:
				vehicles = BigWorld.player().arena.vehicles
				for id in vehicles:
					vehicle = vehicles[id]
					log.LOG_DEBUG("Found player from battle", vehicle["name"])
					yield dict(name=vehicle["name"], id=vehicle["accountDBID"])
			except AttributeError:
				pass
		if in_prebattle:
			try:
				# get players from Team Battle room
				for unit in BigWorld.player().unitMgr.units.itervalues():
					for id, player in unit.getPlayers().iteritems():
						log.LOG_DEBUG("Found player from unit", player["nickName"])
						yield dict(name=player["nickName"], id=id)
			except AttributeError:
				pass
			try:
				# get players from Training Room and the like
				for roster in BigWorld.player().prebattle.rosters.itervalues():
					for info in roster.itervalues():
						log.LOG_DEBUG("Found player from rosters", info["name"])
						yield dict(name=info["name"], id=info["dbID"])
			except AttributeError:
				pass
		users_storage = storage_getter('users')()
		if clanmembers:
			for member in users_storage.getClanMembersIterator(False):
				log.LOG_DEBUG("Found clan member", member.getName())
				yield dict(name=member.getName(), id=member.getID())
		if friends:
			for friend in users_storage.getList(FriendsFindCriteria()):
				log.LOG_DEBUG("Found friend", friend.getName())
				yield dict(name=friend.getName(), id=friend.getID())

class Notifications(object):

	__enabled = True
	__event_handlers = []

	@classmethod
	def init(cls):
		patch_instance_method(NotificationMVC.g_instance, "handleAction", cls.__handleAction)

	@classmethod
	def set_enabled(cls, enabled):
		cls.__enabled = enabled

	@classmethod
	def add_event_handler(cls, action, handler):
		cls.__event_handlers.append((action, handler))

	@classmethod
	def show_info_message(cls, message):
		cls.__show_system_message(message, SystemMessages.SM_TYPE.Information)

	@classmethod
	def show_warning_message(cls, message):
		cls.__show_system_message(message, SystemMessages.SM_TYPE.Warning)

	@classmethod
	def show_error_message(cls, message):
		cls.__show_system_message(message, SystemMessages.SM_TYPE.Error)

	@classmethod
	def show_custom_message(cls, icon="", message="", buttons_layout=[], item={}):
		cls.__push_notification(cls.__MessageDecorator(
			entity_id = cls.__get_new_message_id(),
			icon = icon,
			message = message,
			buttons_layout = buttons_layout,
			item = item
		))

	@classmethod
	def update_custom_message(cls, type_id, msg_id, data):
		model = NotificationMVC.g_instance.getModel()
		if model is not None:
			notification = model.collection.getItem(type_id, msg_id)
			if notification:
				notification.update(data)
			model.updateNotification(type_id, msg_id, data, False)

	@classmethod
	def __handleAction(cls, original_method, self, typeID, entityID, action):
		if action in map(lambda x: x[0], cls.__event_handlers):
			notification = self.getModel().collection.getItem(typeID, entityID)
			if notification:
				for handler_action, handler in cls.__event_handlers:
					if action == handler_action:
						handler(typeID, entityID, notification.get_item())
		else:
			original_method(typeID, entityID, action)

	@classmethod
	def __push_notification(cls, notification):
		model = NotificationMVC.g_instance.getModel()
		if model is None:
			EventLoop.callback(1, cls.__push_notification, notification)
		elif cls.__enabled:
			model.addNotification(notification)

	@classmethod
	def __get_new_message_id(cls):
		try:
			return SystemMessages.g_instance.proto.serviceChannel._ServiceChannelManager__idGenerator.next()
		except AttributeError:
			return 0

	@classmethod
	def __show_system_message(cls, message, type):
		try:
			if SystemMessages.g_instance is None:
				EventLoop.callback(1, cls.__show_system_message, message, type)
			elif cls.__enabled:
				SystemMessages.pushMessage(message, type)
		except:
			log.LOG_CURRENT_EXCEPTION()

	class __MessageDecorator(_NotificationDecorator):

		def __init__(self, entity_id, icon, message, buttons_layout, item):
			self.__item = None
			self.__icon = icon
			self.__message = message
			self.__buttons_layout = buttons_layout
			_NotificationDecorator.__init__(self, entity_id, item, NotificationGuiSettings(isNotify=True, showAt=BigWorld.time()))

		def getType(self):
			return NOTIFICATION_TYPE.MESSAGE

		def update(self, item):
			self._make(item)

		def get_item(self):
			return self.__item

		def _make(self, item = None, settings = None):
			_NotificationDecorator._make(self, item, settings)
			self.__item = item
			buttons_layout = []
			for button in self.__buttons_layout:
				buttons_layout.append({
					"label": button.get("label", "") % item,
					"action": button.get("action", "") % item,
					"type": button.get("type", "submit") % item
				})
			message = {
				"bgIcon": "",
				"defaultIcon": "",
				"icon": self.__icon % item,
				"savedData": 0,
				"timestamp": -1,
				"type": "black",
				"message": self.__message % item,
				"filters": [],
				"buttonsLayout": buttons_layout
			}
			self._vo = {
				"typeID": self.getType(),
				"entityID": self.getID(),
				"message": message,
				"notify": self.isNotify(),
				"auxData": ["GameGreeting"]
			}

class VoiceChat(object):

	@classmethod
	def set_player_speaking(cls, player_id, speaking):
		VOIP.getVOIPManager().onPlayerSpeaking(player_id, speaking)

	@classmethod
	def patch_is_participant_speaking(cls, patch_function):
		original_method = VOIPManager.isParticipantTalking
		def wrapper_isParticipantTalking(self, dbid):
			return patch_function(self, original_method, dbid)
		VOIPManager.isParticipantTalking = wrapper_isParticipantTalking