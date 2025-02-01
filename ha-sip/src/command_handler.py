from __future__ import annotations

import collections.abc
import sys
import os
import json
import time
from typing import Optional

import logging

import pjsua2 as pj

import account
import call
import command_client
import ha
import state
import utils
from call_state_change import CallStateChange
from constants import DEFAULT_RING_TIMEOUT

from ha_mqtt_discoverable import Settings, DeviceInfo, Discoverable
from ha_mqtt_discoverable.sensors import Button, ButtonInfo, Sensor, SensorInfo

class CommandHandler(object):
    def __init__(
        self,
        end_point: pj.Endpoint,
        sip_accounts: dict[int, account.Account],
        call_state: state.State,
        ha_config: ha.HaConfig
    ):
        self.end_point = end_point
        self.sip_accounts = sip_accounts
        self.ha_config = ha_config
        self.call_state = call_state

        broker_address = os.environ.get('BROKER_ADDRESS', '')
        port = utils.convert_to_int(os.environ.get('BROKER_PORT', '1833'))
        mqtt_username = os.environ.get('BROKER_USERNAME', '')
        mqtt_password = os.environ.get('BROKER_PASSWORD', '')

        self._mqtt_settings = Settings.MQTT(host=broker_address, port=port, username=mqtt_username, password=mqtt_password)

        self.call_state_sensor = None

        self.create_ha_devices()

    def create_ha_devices(self):
        device_info = DeviceInfo(
            name="HA-SIP",
            identifiers="ha-sip-2.0",
            manufacturer="Gollam",
            model="Software SIP client",
            sw_version="2.0"
        )

        answer_and_hangup_button_info = ButtonInfo(name="answer_and_hangup_sip_call", device=device_info, unique_id="ha-sip-answer_and_hangup-button")
        answer_and_hangup_button_settings = Settings(mqtt=self._mqtt_settings, entity=answer_and_hangup_button_info)
        answer_and_hangup_button = Button(answer_and_hangup_button_settings, self.answer_and_hangup_button_callback)
        answer_and_hangup_button.write_config()

        call_state_sensor_info = SensorInfo(name="ha_sip_call_state", device=device_info, unique_id="ha-sip-call-state")
        call_state_sensor_settings = Settings(mqtt=self._mqtt_settings, entity=call_state_sensor_info)
        self.call_state_sensor = Sensor(call_state_sensor_settings)

        self.call_state_sensor.set_state("idle")

    def answer_and_hangup_button_callback(self, client, user_data, message):
        logging.debug("answer and hangup button callback called")
        json_str = '{"command": "answer", "number": "10010100000"}'
        command = json.loads(json_str)
        self.handle_command(command, None)

        time.sleep(1)
        json_str = '{"command": "hangup", "number": "10010100000"}'
        command = json.loads(json_str)
        self.handle_command(command, None)
        logging.debug("answer and hangup button callback END")

    def get_call_from_state(self, caller_id: str) -> Optional[call.Call]:
        return self.call_state.get_call(caller_id)

    def get_call_from_state_unsafe(self, caller_id: str) -> call.Call:
        return self.call_state.get_call_unsafe(caller_id)

    def is_active(self, caller_id: str) -> bool:
        return self.call_state.is_active(caller_id)

    def on_state_change(self, state_change: CallStateChange, caller_id: str, new_call: call.Call) -> None:
        self.call_state.on_state_change(state_change, caller_id, new_call)

    def handle_command(self, command: command_client.Command, from_call: Optional[call.Call]) -> None:
        if not isinstance(command, collections.abc.Mapping):
            logging.error('Not an object: %s', command)
            return
        verb = command.get('command')
        number_unknown_type = command.get('number')
        number = str(number_unknown_type) if number_unknown_type is not None else None
        match verb:
            case 'call_service' | None:
                domain = command.get('domain')
                service = command.get('service')
                entity_id = command.get('entity_id')
                service_data = command.get('service_data')
                if (not domain) or (not service) or (not entity_id):
                    logging.error('one of domain, service or entity_id was not provided')
                    return
                logging.info('Calling home assistant service on domain %s service %s with entity %s', domain, service, entity_id)
                try:
                    ha.call_service(self.ha_config, domain, service, entity_id, service_data)
                except Exception as e:
                    logging.error('Error calling home-assistant service: %s', e)
            case 'dial':
                if not number:
                    logging.error('Missing number for command "dial"')
                    return
                logging.info('Got "dial" command for %s', number)
                if self.is_active(number):
                    logging.warning('call already in progress: %s', number)
                    return
                menu = command.get('menu')
                ring_timeout = utils.convert_to_float(command.get('ring_timeout'), DEFAULT_RING_TIMEOUT)
                sip_account_number = utils.convert_to_int(command.get('sip_account'), -1)
                webhook_to_call = command.get('webhook_to_call_after_call_was_established')
                webhooks = command.get('webhook_to_call')
                sip_account = self.sip_accounts.get(sip_account_number, next(iter(self.sip_accounts.values())))
                call.make_call(self.end_point, sip_account, number, menu, self, self.ha_config, ring_timeout, webhook_to_call, webhooks)
            case 'hangup':
                if not number:
                    logging.error('Missing number for command "hangup"')
                    return
                logging.info('Got "hangup" command for %s', number)
                if not self.is_active(number):
                    self.call_not_in_progress_error(number)
                    return
                current_call = self.get_call_from_state_unsafe(number)
                current_call.hangup_call()
            case 'answer':
                if not number:
                    logging.error('Missing number for command "answer"')
                    return
                logging.info('Got "answer" command for %s', number)
                if not self.is_active(number):
                    self.call_not_in_progress_error(number)
                    return
                menu = command.get('menu')
                current_call = self.get_call_from_state_unsafe(number)
                current_call.answer_call(menu)
            case 'transfer':
                if not number:
                    logging.error('Missing number for command "transfer"')
                    return
                transfer_to = command.get('transfer_to')
                if not transfer_to:
                    logging.error('Missing transfer_to for command "transfer_to"')
                    return
                if not self.is_active(number):
                    self.call_not_in_progress_error(number)
                    return
                current_call = self.get_call_from_state_unsafe(number)
                current_call.transfer(transfer_to)
            case 'bridge_audio':
                if not number:
                    logging.error('Missing number for command "bridge_audio"')
                    return
                bridge_to = command.get('bridge_to')
                if not bridge_to:
                    logging.error('Missing bridge_to for command "bridge_audio"')
                    return
                call_one = from_call if number == 'self' else self.get_call_from_state(number)
                call_two = from_call if bridge_to == 'self' else self.get_call_from_state(bridge_to)
                if not call_one:
                    self.call_not_in_progress_error(number)
                    return
                if not call_two:
                    self.call_not_in_progress_error(bridge_to)
                    return
                call_one.bridge_audio(call_two)
            case 'send_dtmf':
                if not number:
                    logging.error('Missing number for command "send_dtmf"')
                    return
                digits = command.get('digits')
                method = command.get('method', 'in_band')
                if (method != 'in_band') and (method != 'rfc2833') and (method != 'sip_info'):
                    logging.error('method must be one of in_band, rfc2833, sip_info')
                    return
                if not digits:
                    logging.error('Missing digits for command "send_dtmf"')
                    return
                logging.info('Got "send_dtmf" command for %s', number)
                if not self.is_active(number):
                    self.call_not_in_progress_error(number)
                    return
                current_call = self.get_call_from_state_unsafe(number)
                current_call.send_dtmf(digits, method)
            case 'play_audio_file':
                if not number:
                    logging.error('Missing number for command "play_audio_file"')
                    return
                if not self.is_active(number):
                    self.call_not_in_progress_error(number)
                    return
                current_call = self.get_call_from_state_unsafe(number)
                audio_file = command.get('audio_file')
                if not audio_file:
                    logging.error('Missing parameter "audio_file" for command "play_audio_file"')
                    return
                cache_audio = command.get('cache_audio') or False
                wait_for_audio_to_finish = command.get('wait_for_audio_to_finish') or False
                current_call.play_audio_file(audio_file, cache_audio, wait_for_audio_to_finish)
            case 'play_message':
                if not number:
                    logging.error('Missing number for command "play_message"')
                    return
                if not self.is_active(number):
                    self.call_not_in_progress_error(number)
                    return
                current_call = self.get_call_from_state_unsafe(number)
                message = command.get('message')
                if not message:
                    logging.error('Missing parameter "message" for command "play_message"')
                    return
                tts_language = command.get('tts_language') or self.ha_config.tts_language
                cache_audio = command.get('cache_audio') or False
                wait_for_audio_to_finish = command.get('wait_for_audio_to_finish') or False
                current_call.play_message(message, tts_language, cache_audio, wait_for_audio_to_finish)
            case 'stop_playback':
                if not number:
                    logging.error('Missing number for command "stop_playback"')
                    return
                if not self.is_active(number):
                    self.call_not_in_progress_error(number)
                    return
                current_call = self.get_call_from_state_unsafe(number)
                current_call.stop_playback()
            case 'state':
                self.call_state.output()
            case 'quit':
                logging.info('Quit.')
                self.end_point.libDestroy()
                sys.exit(0)
            case _:
                logging.error('Error: Unknown command: %s', verb)

    def call_not_in_progress_error(self, number: str):
        logging.warning('Warning: call not in progress: %s', number)
        self.call_state.output()
