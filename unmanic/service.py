#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    unmanic.service.py

    Written by:               Josh.5 <jsunnex@gmail.com>
    Date:                     06 Dec 2018, (7:21 AM)

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

           THE SOFTWARE IS PROVIDED 'AS IS', WITHOUT WARRANTY OF ANY KIND,
           EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
           MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
           IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
           DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
           OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
           OR OTHER DEALINGS IN THE SOFTWARE.

"""
import argparse
import os
import queue
import signal
import time
import threading

import psutil

from unmanic import config, metadata
from unmanic.libs import libraryscanner, common, eventmonitor
from unmanic.libs.db_migrate import Migrations
from unmanic.libs.logs import UnmanicLogging
from unmanic.libs.scheduler import ScheduledTasksManager
from unmanic.libs.taskqueue import TaskQueue
from unmanic.libs.postprocessor import PostProcessor
from unmanic.libs.taskhandler import TaskHandler
from unmanic.libs.uiserver import FrontendPushMessages, UIServer
from unmanic.libs.foreman import Foreman


def init_db(config_path):
    # Set paths
    app_dir = os.path.dirname(os.path.abspath(__file__))

    # Set database connection settings
    database_settings = {
        "TYPE":                       "SQLITE",
        "FILE":                       os.path.join(config_path, 'unmanic.db'),
        "MIGRATIONS_DIR":             os.path.join(app_dir, 'migrations_v1'),
        "MIGRATIONS_HISTORY_VERSION": 'v1',
    }

    # Ensure the config path exists
    if not os.path.exists(config_path):
        os.makedirs(config_path)

    # Create database connection
    from unmanic.libs.unmodels.lib import Database
    db_connection = Database.select_database(database_settings)

    # Run database migrations
    migrations = Migrations(database_settings)
    migrations.update_schema()

    # Return the database connection
    return db_connection


class RootService:

    def __init__(self):
        self.threads = []
        self.run_threads = True
        self.db_connection = None

        self.developer = None
        self.dev_api = None

        self.logger = UnmanicLogging.get_logger(name=__class__.__name__)
        UnmanicLogging.metric("root_service_started")

        self.event = threading.Event()

        self._mgr = None

    def start_handler(self, data_queues, task_queue):
        self.logger.info("Starting TaskHandler")
        handler = TaskHandler(data_queues, task_queue, self.event)
        handler.daemon = True
        handler.start()
        self.threads.append({
            'name':   'TaskHandler',
            'thread': handler
        })
        return handler

    def start_post_processor(self, data_queues, task_queue):
        self.logger.info("Starting PostProcessor")
        postprocessor = PostProcessor(data_queues, task_queue, self.event)
        postprocessor.daemon = True
        postprocessor.start()
        self.threads.append({
            'name':   'PostProcessor',
            'thread': postprocessor
        })
        return postprocessor

    def start_foreman(self, data_queues, settings, task_queue):
        self.logger.info("Starting Foreman")
        foreman = Foreman(data_queues, settings, task_queue, self.event)
        foreman.daemon = True
        foreman.start()
        self.threads.append({
            'name':   'Foreman',
            'thread': foreman
        })
        return foreman

    def start_library_scanner_manager(self, data_queues):
        self.logger.info("Starting LibraryScannerManager")
        library_scanner_manager = libraryscanner.LibraryScannerManager(data_queues, self.event)
        library_scanner_manager.daemon = True
        library_scanner_manager.start()
        self.threads.append({
            'name':   'LibraryScannerManager',
            'thread': library_scanner_manager
        })
        return library_scanner_manager

    def start_inotify_watch_manager(self, data_queues, settings):
        if eventmonitor.event_monitor_module:
            self.logger.info("Starting EventMonitorManager")
            event_monitor_manager = eventmonitor.EventMonitorManager(data_queues, self.event)
            event_monitor_manager.daemon = True
            event_monitor_manager.start()
            self.threads.append({
                'name':   'EventMonitorManager',
                'thread': event_monitor_manager
            })
            return event_monitor_manager
        else:
            self.logger.warn("Unable to start EventMonitorManager as no event monitor module was found")

    def start_ui_server(self, data_queues, foreman):
        self.logger.info("Starting UIServer")
        uiserver = UIServer(data_queues, foreman, self.developer)
        uiserver.daemon = True
        uiserver.start()
        self.threads.append({
            'name':   'UIServer',
            'thread': uiserver
        })
        return uiserver

    def start_scheduled_tasks_manager(self):
        self.logger.info("Starting ScheduledTasksManager")
        scheduled_tasks_manager = ScheduledTasksManager(self.event)
        scheduled_tasks_manager.daemon = True
        scheduled_tasks_manager.start()
        self.threads.append({
            'name':   'ScheduledTasksManager',
            'thread': scheduled_tasks_manager
        })
        return scheduled_tasks_manager

    def start_resource_logger(self):
        abort_flag = threading.Event()

        def log_resources():
            pid = os.getpid()
            proc = psutil.Process(pid)
            cpu_count = psutil.cpu_count(logical=True)
            start_time = time.time()

            while not self.event.is_set() and not abort_flag.is_set():
                try:
                    # Fetch CPU info
                    cpu_percent = proc.cpu_percent(interval=None)
                    normalised_cpu_percent = cpu_percent / cpu_count

                    # Fetch Memory info
                    mem_info = proc.memory_info()
                    rss_bytes = mem_info.rss
                    vms_bytes = mem_info.vms

                    # Calculate percentage of memory used relative to total system RAM
                    total_system_ram = psutil.virtual_memory().total
                    mem_percent = (rss_bytes / total_system_ram) * 100

                    # Calculate uptime in seconds
                    uptime = int(time.time() - start_time)

                    UnmanicLogging.metric("root_service_resources",
                                          pid=pid,
                                          uptime=uptime,
                                          cpu_percent=normalised_cpu_percent,
                                          mem_percent=mem_percent,
                                          rss_bytes=rss_bytes,
                                          vms_bytes=vms_bytes)
                except Exception as e:
                    self.logger.warning(f"Resource logging failed: {e}")
                    time.sleep(5)
                    continue

                time.sleep(5)  # Polling interval

        thread = threading.Thread(
            target=log_resources,
            name='RootServiceResourceLogger',
            daemon=True
        )
        thread.stop = abort_flag.set
        thread.start()
        self.threads.append({
            'name':   'RootServiceResourceLogger',
            'thread': thread
        })

    def initial_register_unmanic(self):
        from unmanic.libs import session
        s = session.Session(dev_api=self.dev_api)
        s.register_unmanic(s.get_installation_uuid())

    def start_threads(self, settings):
        # Create our data queues
        data_queues = {
            "library_scanner_triggers": queue.Queue(maxsize=1),
            "scheduledtasks":           queue.Queue(),
            "inotifytasks":             queue.Queue(),
            "progress_reports":         queue.Queue(),
            "frontend_messages":        FrontendPushMessages(),
        }

        # Clear cache directory
        self.logger.info("Clearing previous cache")
        common.clean_files_in_cache_dir(settings.get_cache_path())

        self.logger.info("Starting all threads")

        # Register installation
        self.initial_register_unmanic()

        # Setup job queue
        task_queue = TaskQueue(data_queues)

        # Setup post-processor thread
        self.start_post_processor(data_queues, task_queue)

        # Start the foreman thread
        foreman = self.start_foreman(data_queues, settings, task_queue)

        # Start new thread to handle messages from service
        self.start_handler(data_queues, task_queue)

        # Start scheduled thread
        self.start_library_scanner_manager(data_queues)

        # Start inotify watch manager
        self.start_inotify_watch_manager(data_queues, settings)

        # Start new thread to run the web UI
        self.start_ui_server(data_queues, foreman)

        # Start new thread to run the scheduled tasks manager
        self.start_scheduled_tasks_manager()

        # Start main thread resource logger
        self.start_resource_logger()

    def stop_threads(self):
        self.logger.info("Stopping all threads")
        self.event.set()
        for thread in self.threads:
            self.logger.info("Sending thread {} abort signal".format(thread['name']))
            thread['thread'].stop()
        for thread in self.threads:
            self.logger.info("Waiting for thread {} to stop".format(thread['name']))
            thread['thread'].join(10)
            self.logger.info("Thread {} has successfully stopped".format(thread['name']))
        self.threads = []

    def sig_handle(self, signum, frame):
        self.logger.info("Received {}".format(signum))
        self.stop()

    def stop(self):
        self.run_threads = False

    def run(self):
        # Init the TaskDataStore and PluginChildProcess
        import tornado.autoreload
        from multiprocessing import Manager
        import atexit
        from unmanic.libs.task import TaskDataStore
        from unmanic.libs.unplugins.child_process import kill_all_plugin_processes, set_shared_manager
        # Init a shared manager
        self._mgr = Manager()
        # Ensure Manager shuts down on process exit or tornado autoreload (dev mode)
        atexit.register(self._mgr.shutdown)
        tornado.autoreload.add_reload_hook(self._mgr.shutdown)
        # Ensure any PluginChildProcess shuts down on process exit or tornado autoreload (dev mode)
        atexit.register(kill_all_plugin_processes)
        tornado.autoreload.add_reload_hook(kill_all_plugin_processes)
        # Replace the in-process dicts with manager proxies
        TaskDataStore._runner_state = self._mgr.dict()
        TaskDataStore._task_state = self._mgr.dict()
        # Set the shared manager for PluginChildProcess
        set_shared_manager(self._mgr)

        # Init the configuration
        settings = config.Config()

        # Init the database
        self.db_connection = init_db(settings.get_config_path())

        # Start all threads
        self.start_threads(settings)

        # Watch for the term signal
        if os.name == "nt":
            while self.run_threads:
                try:
                    time.sleep(1)
                except (KeyboardInterrupt, SystemExit) as e:
                    break
        else:
            signal.signal(signal.SIGINT, self.sig_handle)
            signal.signal(signal.SIGTERM, self.sig_handle)
            while self.run_threads:
                signal.pause()
                time.sleep(.5)

        # Received term signal. Stop everything
        self.stop_threads()
        self.db_connection.stop()
        while not self.db_connection.is_stopped():
            time.sleep(.5)
            continue
        self.logger.info("Exit Unmanic")


def main():
    parser = argparse.ArgumentParser(description='Unmanic')
    parser.add_argument('--version', action='version',
                        version='%(prog)s {version}'.format(version=metadata.read_version_string('long')))
    parser.add_argument('--manage_plugins', action='store_true',
                        help='manage installed plugins')
    parser.add_argument('--dev',
                        action='store_true',
                        help='Enable developer mode')
    parser.add_argument('--dev-api', nargs='?',
                        help='Enable development against another unmanic support api')
    parser.add_argument('--port', nargs='?',
                        help='Specify the port to run the webserver on')
    # parser.add_argument('--unmanic_path', nargs='?',
    #                    help='Specify the unmanic configuration path instead of ~/.unmanic')
    args = parser.parse_args()

    # Configure application from args
    settings = config.Config(
        port=args.port,
        unmanic_path=None
    )

    if args.manage_plugins:
        # Init the DB connection
        db_connection = init_db(settings.get_config_path())

        # Run the plugin manager CLI
        from unmanic.libs.unplugins.pluginscli import PluginsCLI
        plugin_cli = PluginsCLI()
        plugin_cli.run()

        # Stop the DB connection
        db_connection.stop()
        while not db_connection.is_stopped():
            time.sleep(.2)
            continue
    else:
        # Run the main Unmanic service
        service = RootService()
        service.developer = args.dev
        service.dev_api = args.dev_api
        service.run()


if __name__ == "__main__":
    main()
