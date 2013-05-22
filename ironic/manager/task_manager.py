# vim: tabstop=4 shiftwidth=4 softtabstop=4
# coding=utf-8

# Copyright 2013 Hewlett-Packard Development Company, L.P.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
A context manager to peform a series of tasks on a set of resources.

TaskManagers are context managers, created on-demand to synchronize locking and
simplify operations across a set of ResourceManagers.  Each ResourceManager
holds the data model for a node, as well as handles to the controller and
deployer drivers configured for that node.

The TaskManager will acquire either a shared or exclusive lock, as indicated.
Multiple shared locks for the same resource may coexist with an exclusive lock,
but only one exclusive lock will be granted across a deployment; attempting to
allocate another will raise an exception.  An exclusive lock is represented in
the database to coordinate between Managers,  even when deployed on different
hosts.

TaskManager methods, as well as driver methods, may be decorated to determine
whether their invokation requires an exclusive lock.  For example:

    from ironic.manager import task_manager

    node_ids = [1, 2, 3]
    try:
        # Get an exclusive lock so we can manage power state.
        # This is the default behaviour of acquire_nodes.
        with task_manager.acquire(node_ids) as task:
            task.power_on()
            states = task.get_power_state()
    except exception.NodeLocked:
        LOG.info(_("Unable to power nodes on."))
        # Get a shared lock, just to check the power state.
        with task_manager.acquire(node_ids, shared=True) as task:
            states = nodes.get_power_state()


In case TaskManager does not provide a method wrapping a particular driver
function, you can access the drivers directly in this way:

    with task_manager.acquire(node_ids) as task:
        states = []
        for node, control, deploy in [r.node, r.controller, r.deployer
                                        for r in task.resources]:
            # control and deploy are driver singletons,
            # loaded based on that node's configuration.
            states.append(control.get_power_state(task, node)
"""

import contextlib
from oslo.config import cfg

from ironic.common import exception
from ironic.db import api as dbapi
from ironic.manager import resource_manager

CONF = cfg.CONF


def require_exclusive_lock(f):
    """Decorator to require an exclusive lock."""
    def wrapper(*args, **kwargs):
        tracker = args[0]
        if tracker.shared:
            raise exception.ExclusiveLockRequired()
        return f(*args, **kwargs)
    return wrapper


@contextlib.contextmanager
def acquire(node_ids, shared=False):
    """Acquire either a shared or exclusive lock on the supplied node_ids."""

    t = TaskManager(shared)

    try:
        if not shared:
            t.dbapi.reserve_nodes(CONF.host, node_ids)
        for id in node_ids:
            t.resources.append(resource_manager.NodeManager.acquire(id, t))
        yield t
    finally:
        for id in [r.id for r in t.resources]:
            resource_manager.NodeManager.release(id, t)
        if not shared:
            t.dbapi.release_nodes(CONF.host, node_ids)


class TaskManager(object):
    """Context manager for tasks."""

    def __init__(self, shared):
        self.shared = shared
        self.resources = []
        self.dbapi = dbapi.get_instance()
