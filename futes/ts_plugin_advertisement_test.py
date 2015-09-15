from helpers.testcasebase import TestCaseBase, TS_PLUGIN_INSTALLER_PATH
from helpers.utils import *
import mock
import os
import nosepipe

@nosepipe.isolate
class TSPluginAdvertisement(TestCaseBase):
	'''
	This fute test tests TessuMod Plugin is advertised in lobby.
	To execute, use command:
		$ nosetests --with-process-isolation
	'''

	def setUp(self):
		TestCaseBase.setUp(self)
		import notification
		self.mock_addNotification = notification.NotificationMVC.g_instance.getModel().addNotification = mock.Mock(
			wraps=notification.NotificationMVC.g_instance.getModel().addNotification
		)

	def __is_advertisement_shown(self):
		return mock_was_called_with(self.mock_addNotification, message_decorator_matches_fragments(
			["TessuModTSPluginInstall", "TessuModTSPluginMoreInfo", "TessuModTSPluginIgnore"]))

	def test_ts_plugin_advertisement_is_shown(self):
		self.start_ts_client()
		self.start_game(mode="lobby")
		self.run_in_event_loop(verifiers=[
			lambda: self.__is_advertisement_shown()
		])

	def test_ts_plugin_advertisement_is_not_shown_if_already_installed(self):
		self.start_ts_client()
		self.enable_ts_client_tessumod_plugin(version=1)
		self.start_game(mode="lobby")
		self.run_in_event_loop(min_wait=5, verifiers=[
			lambda: not self.__is_advertisement_shown()
		])

	def test_ts_plugin_advertisement_is_not_shown_if_installer_is_missing(self):
		self.start_ts_client()
		os.remove(TS_PLUGIN_INSTALLER_PATH)
		self.start_game(mode="lobby")
		self.run_in_event_loop(min_wait=5, verifiers=[
			lambda: not self.__is_advertisement_shown()
		])

	def test_ts_plugin_advertisement_is_not_shown_if_ignored(self):
		self.start_ts_client()
		self.change_mod_state_variables(ignored_plugin_version=1)
		self.start_game(mode="lobby")
		self.run_in_event_loop(min_wait=5, verifiers=[
			lambda: not self.__is_advertisement_shown()
		])

	@mock.patch("subprocess.call")
	def test_install_button_starts_plugin_installer(self, subprocess_call_mock):
		self.start_ts_client()
		self.start_game(mode="lobby")
		import notification
		def on_notification(msg):
			notification.NotificationMVC.g_instance.handleAction(typeID=msg.getType(), entityID=msg.getID(), action="TessuModTSPluginInstall")
		notification.NotificationMVC.g_instance.futes_on_add_notification += on_notification
		self.run_in_event_loop(min_wait=5, verifiers=[
			lambda: mock_was_called_with(subprocess_call_mock, args=[contains_match("tessumod.ts3_plugin")], shell=True)
		])
