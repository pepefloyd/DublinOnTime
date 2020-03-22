import json
import logging
import random
from enum import Enum, IntEnum

import falcon
import pandas as pandas
import requests
from pydialogflow_fulfillment import DialogflowResponse, DialogflowRequest, SimpleResponse

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)
api = bus_app = falcon.API()
bus_stop_route = '/busstop'


class APIException(Exception):
    pass


class Availability(Enum):
    """Represents the state of the bus availability"""
    ONE_BUS = 1
    MANY_BUSES = 2
    NO_BUSES = 3


class Constants(IntEnum):
    """Limits in this module"""
    MAX_BUSES = 5


class BusStopRequest():
    """Represents a request to this API"""

    def on_post(self, req, resp):
        """Handles POST requests"""
        try:
            logger.info('Received a new request')
            json_request = json.load(req.bounded_stream)
            dlg_flow_req = DialogflowRequest(json.dumps(json_request))
            stop_param = '' if 'stop' not in dlg_flow_req.get_parameters() \
                else dlg_flow_req.get_parameters().get('stop')
            if dlg_flow_req.get_action() == 'call_busstop_api' and stop_param:
                logger.info('stop parameter is {}'.format(stop_param))
                bus_stop = int(stop_param.split('.', 1)[0])
                logger.info("Received request for stop {}".format(bus_stop))
                query_response = self.query_bus_stop(bus_stop)
                bus_times_response_state = self.deserialize_response(query_response)
                api_response = BusStopResponse(bus_times_response_state).response_message
                logger.info('Setting response to {}'.format(api_response))
                resp.body = self.create_dialogflow_response(api_response)
                resp.content_type = falcon.MEDIA_JSON
                resp.status = falcon.HTTP_200
            elif dlg_flow_req.get_action() == 'call_busstop_api':
                logger.info('Action is: ask for bus stop')
                resp.body = self.create_dialogflow_response(BusStopResponse.get_greeting_with_question(), True)
                resp.content_type = falcon.MEDIA_JSON
                resp.status = falcon.HTTP_200
            else:
                raise APIException()
        except Exception as error:
            logger.error(str(error))
            resp.body = self.create_dialogflow_response(BusStopResponse.get_error_message())
            resp.content_type = falcon.MEDIA_JSON
            resp.status = falcon.HTTP_200

    def create_dialogflow_response(self,response, expect_user_response=False):

        dialogflow_response = DialogflowResponse()
        dialogflow_response.expect_user_response = expect_user_response
        dialogflow_response.add(SimpleResponse(response, response))
        return dialogflow_response.get_final_response()

    def query_bus_stop(self, stop_number):
        return self.send_request(self.get_rtpi_site() + '?stopRef=' + str(stop_number))

    @staticmethod
    def get_rtpi_site():
        """ Get the actual RTPI site"""
        return 'http://rtpi.ie/Text/WebDisplay.aspx'

    @staticmethod
    def send_request(full_query):
        """
        Send a request to the RTPI site containing the full site
        and stop we are looking for
        :param full_query: site + query string with bus stop on it
        :return:
        """
        return requests.get(full_query)

    @staticmethod
    def deserialize_response(raw_response):
        """
        Parse the response into a Pandas dataframe
        """
        body = raw_response.content.decode('utf-8')
        tables = pandas.read_html(body)  # Returns list of all tables on page
        html_table = tables[0]  # Grab the first table we are interested in
        service_times = html_table[['Service', 'Time']]
        return service_times


class BusStopResponse():
    """
    Represents a response to the client from this API
    """
    greetings = ['Welcome to Dublin on time. Please tell me the '
                 'bus stop number you would like me to check for you',
                 'Hello!, please tell me the stop number you would like me to verify',
                 'Hey there, please tell me the stop number you need me to check',
                 'Hi, please tell me the stop number']

    error_messages = ['Sorry, I could not find this information. Please try later.',
                      'It was not possible to get this information. Please try again later',]

    single_bus_message = ['One bus is coming.', 'There is one bus coming  ']

    many_buses_message = ['There buses are coming ', 'The following buses are coming  ',
                          'These are the buses coming to this stop']
    def __init__(self, bus_response):

        self.bus_response = bus_response
        self.availability = None
        self.response_message = ''
        self.set_availability()
        self.set_message()

    @classmethod
    def get_greeting_with_question(cls):
        return random.choice(cls.greetings)

    @classmethod
    def get_error_message(cls):
        return random.choice(cls.error_messages)
    @classmethod
    def get_single_bus_message_initial_greeting(cls):
        return random.choice(cls.single_bus_message)

    @classmethod
    def get_many_buses_initial_greeting(cls):
        return random.choice(cls.many_buses_message)

    def set_message(self):
        """Sets the correct message"""

        if self.availability == Availability.MANY_BUSES:
            self.response_message = self.get_many_buses_initial_greeting()
        elif self.availability == Availability.ONE_BUS:
            self.response_message = self.get_single_bus_message_initial_greeting()
        else:
            self.response_message = 'I could not find any buses arriving at this bus stop.'

        if self.availability == Availability.MANY_BUSES or self.availability == Availability.ONE_BUS:
            bus_details_message = ', '.join(self.get_incoming_buses_detailed_message(self.bus_response))
            self.response_message = self.response_message + bus_details_message

    def set_availability(self):
        try:
            if self.bus_response.Service.size > 1:
                self.availability = Availability.MANY_BUSES
                if self.bus_response.Service.size > Constants.MAX_BUSES:
                    self.bus_response = self.bus_response.head(Constants.MAX_BUSES)
            elif self.bus_response.Service.size == 1:
                self.availability = Availability.ONE_BUS
            else:
                self.availability = Availability.NO_BUSES
        except Exception:
            self.availability = Availability.NO_BUSES

    @staticmethod
    def get_incoming_buses_detailed_message(bus_response):
        """ Gets a user friendly message with bus service information"""

        def is_time(time_value):
            """
            Check if the reply had a time-like value such as 22:05
            """
            return ":" in str(time_value)

        def is_due(time_value):
            """
            check if we value is 'due'
            """
            return str(time_value).lower() == 'due'

        def prepare_message(service_time):
            """
            Prepare a user-friendly message
            """
            message = service_time[0] + ' '
            if is_due(service_time[1]):
                message += ' is due'
            elif is_time(service_time[1]):
                # 22:05 changes to '22 05'
                message += ' is coming at ' + service_time[1].replace(':', ' ')
            else:
                message += service_time[1].replace('Mins', 'minutes')
            return message

        resp = bus_response
        service_time_message = [prepare_message((service, time)) for service, time in zip(resp['Service'], resp['Time'])]
        return service_time_message


def add_api_endpoints(endpoint):
    """ Add the available endpoints for this API"""
    bus_app.add_route(endpoint, BusStopRequest())


add_api_endpoints(bus_stop_route)
