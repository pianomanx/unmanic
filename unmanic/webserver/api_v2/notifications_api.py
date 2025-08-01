#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    unmanic.notifications_api.py

    Written by:               Josh.5 <jsunnex@gmail.com>
    Date:                     20 Apr 2022, (1:07 AM)

    Copyright:
           Copyright (C) Josh Sunnex - All Rights Reserved

           Permission is hereby granted, free of charge, to any person obtaining a copy
           of this software and associated documentation files (the "Software"), to deal
           in the Software without restriction, including without limitation the rights
           to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
           copies of the Software, and to permit persons to whom the Software is
           furnished to do so, subject to the following conditions:

           The above copyright notice and this permission notice shall be included in all
           copies or substantial portions of the Software.

           THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
           EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
           MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
           IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
           DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
           OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
           OR OTHER DEALINGS IN THE SOFTWARE.

"""

import tornado.log
from unmanic import config
from unmanic.libs import session
from unmanic.libs.notifications import Notifications
from unmanic.libs.uiserver import UnmanicDataQueues
from unmanic.webserver.api_v2.base_api_handler import BaseApiError, BaseApiHandler
from unmanic.webserver.api_v2.schema.schemas import RequestNotificationsDataSchema, RequestTableUpdateByIdList, \
    RequestTableUpdateByUuidList


class ApiNotificationsHandler(BaseApiHandler):
    session = None
    config = None
    params = None
    unmanic_data_queues = None

    routes = [
        {
            "path_pattern":      r"/notifications/read",
            "supported_methods": ["GET"],
            "call_method":       "get_notifications",
        },
        {
            "path_pattern":      r"/notifications/remove",
            "supported_methods": ["DELETE"],
            "call_method":       "remove_notifications",
        },
    ]

    def initialize(self, **kwargs):
        self.session = session.Session()
        self.params = kwargs.get("params")
        udq = UnmanicDataQueues()
        self.unmanic_data_queues = udq.get_unmanic_data_queues()
        self.config = config.Config()

    async def get_notifications(self):
        """
        Notifications - read
        ---
        description: Returns a list of notifications in reverse chronological order.
        responses:
            200:
                description: 'Sample response: A list of notifications in reverse chronological order.'
                content:
                    application/json:
                        schema:
                            RequestNotificationsDataSchema
            400:
                description: Bad request; Check `messages` for any validation errors
                content:
                    application/json:
                        schema:
                            BadRequestSchema
            404:
                description: Bad request; Requested endpoint not found
                content:
                    application/json:
                        schema:
                            BadEndpointSchema
            405:
                description: Bad request; Requested method is not allowed
                content:
                    application/json:
                        schema:
                            BadMethodSchema
            500:
                description: Internal error; Check `error` for exception
                content:
                    application/json:
                        schema:
                            InternalErrorSchema
        """
        try:
            notifications = Notifications()
            notifications_list = notifications.read_all_items()
            notifications_list_reversed = list(reversed(notifications_list))

            response = self.build_response(
                RequestNotificationsDataSchema(),
                {
                    "notifications": notifications_list_reversed,
                }
            )
            self.write_success(response)
            return
        except BaseApiError as bae:
            tornado.log.app_log.error("BaseApiError.{}: {}".format(self.route.get('call_method'), str(bae)))
            return
        except Exception as e:
            self.set_status(self.STATUS_ERROR_INTERNAL, reason=str(e))
            self.write_error()

    async def remove_notifications(self):
        """
        Notifications - delete
        ---
        description: Delete one or all notifications.
        requestBody:
            description: Requested list of items to delete.
            required: True
            content:
                application/json:
                    schema:
                        RequestTableUpdateByUuidList
        responses:
            200:
                description: 'Successful request; Returns success status'
                content:
                    application/json:
                        schema:
                            BaseSuccessSchema
            400:
                description: Bad request; Check `messages` for any validation errors
                content:
                    application/json:
                        schema:
                            BadRequestSchema
            404:
                description: Bad request; Requested endpoint not found
                content:
                    application/json:
                        schema:
                            BadEndpointSchema
            405:
                description: Bad request; Requested method is not allowed
                content:
                    application/json:
                        schema:
                            BadMethodSchema
            500:
                description: Internal error; Check `error` for exception
                content:
                    application/json:
                        schema:
                            InternalErrorSchema
        """
        try:
            json_request = self.read_json_request(RequestTableUpdateByUuidList())

            notifications = Notifications()
            for notification_uuid in json_request.get('uuid_list', []):
                if not notifications.remove(notification_uuid):
                    self.set_status(self.STATUS_ERROR_EXTERNAL,
                                    reason="Failed to delete the notification with UUID '{}'".format(notification_uuid))
                    self.write_error()
                    return

            self.write_success()
            return
        except BaseApiError as bae:
            tornado.log.app_log.error("BaseApiError.{}: {}".format(self.route.get('call_method'), str(bae)))
            return
        except Exception as e:
            self.set_status(self.STATUS_ERROR_INTERNAL, reason=str(e))
            self.write_error()
