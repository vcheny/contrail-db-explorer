#! /usr/bin/env python
# PYTHON_ARGCOMPLETE_OK

import os, sys
import gzip
import json
import argparse
from uuid import UUID
import time, calendar
from datetime import datetime
import socket, struct
import pdb
import re
import logging

VERSION = '1.0'

def is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

def dec_to_ip(number, ver=4):
    if ver == 4:
        return socket.inet_ntoa(struct.pack('!L', number))
    else:
        return socket.inet_ntop(socket.AF_INET6,
                struct.pack('!QQ', number>>64, number & ((1 << 64) - 1)))

def is_valid_uuid(id):
    try:
        obj = UUID(str(id))
    except:
        return False
    return str(id)

def ts_to_string(ts):
    # timestampe to string "%Y-%m-%dT%H:%M:%S.%f"
    if len(str(ts)) == 16:
        return datetime.utcfromtimestamp(int(ts)/1000000.0).isoformat()
    elif len(str(ts)) == 13:
        return datetime.utcfromtimestamp(int(ts)/1000.0).isoformat()
    else:
        return datetime.utcfromtimestamp(int(ts)/1.0).isoformat()

def time_type(s, fmt = "%Y-%m-%dT%H:%M:%S"):
    times = 1000000
    try:
        datetime_obj = datetime.strptime(s, fmt)
    except:
        raise argparse.ArgumentTypeError(
            "Wrong time format e.g. 2018-03-19T15:27:01")
    timeInSeconds = calendar.timegm(datetime_obj.utctimetuple())
    return timeInSeconds * times

def dict_compare(d1, d2):
    (d1_only, d2_only, modified) = ({}, {}, {})

    for k, v in d1.items():
        if k not in d2:
            d1_only[k] = v
        elif v != d2[k]:
            modified[k] = (v, d2[k])
    for k, v in d2.items():
        if k not in d1:
            d2_only[k] = v

    return (d1_only, d2_only, modified)


def dict_print(d, offset='', step=0):
    indent = ' ' * 3
    for key, value in d.items():
        if isinstance(value, dict):
            print(offset + indent * step + str(key) + ':')
            dict_print(value, offset, step+1)
        else:
            print(offset + indent * step + str(key) + ': ' + str(value))

def pretty_print(d, offset='', step=0):
    indent = ' ' * 3
    if isinstance(d, dict):
        for key, value in d.items():
            if (isinstance(value, dict) or isinstance(value, list)
                    or isjson(value)):
                print(offset + indent * step + str(key) + ':')
                pretty_print(value, offset, step+1)
            else:
                print(offset + indent * step + str(key) + ': ' + str(value))
    elif isinstance(d, list):
        if len(d):
            if (isinstance(d[0], dict) or isinstance(d[0], list)
                    or isjson(d[0])):
                for e in d:
                    pretty_print(e, offset, step+1)
            else:
                print(offset + indent * step + ':'.join(str(e) for e in d))
    elif isjson(d):
        pretty_print(json.loads(d), offset, step+1)
    else:
        print(offset + indent * step + str(d))

def is_in(key, string):
    assert (isinstance(key, list) or isinstance(key, set)), "expect a list"
    for i in key:
        if i in string:
            return True
    return False

def isjson(j):
    if not(isinstance(j, str) or isinstance(j, unicode)):
        return False
    if len(j) > 1 and (j[0] + j[-1] == '{}' or j[0] + j[-1] == '[]'):
        try:
            json.loads(j)
        except:
            return False
        return True
    return False

class DB():
    """ Class to load config db json file into a dictionary """
    def __init__(self, from_json = True, **kwargs):
        self._logger = logging.getLogger(__name__)
        if from_json:
            self.zk = {}
            self.cassandra = {}
            json_file = kwargs.get('json_file', "db-dump.json")
            self._load_db(json_file)
        # else connect to cassandra and zookeep - TBD

    def _load_db (self, json_file):
        self.zk = {}
        self.cassandra = {}
        db_contents = {}
        self._logger.info('Loading %s ...', json_file)
        if json_file.endswith('.gz'):
            try:
                f = gzip.open(json_file, 'rb')
                db_contents = json.loads(f.read())
            finally:
                f.close()
        else:
            with open(json_file, 'r') as f:
                db_contents = json.loads(f.read())

        self._logger.info('Loading %s completed', json_file)

        self.cassandra = db_contents['cassandra']

        for e in json.loads(db_contents['zookeeper']):
            self.zk[e[0]] = e[1]

class DB_comp():
    """ Class to compare two DB """
    KEYSPACES = ['config_db_uuid',
                'useragent',
                'to_bgp_keyspace',
                'svc_monitor_keyspace',
                'dm_keyspace']

    ZK_PATHS = ['/fq-name-to-uuid/',
                '/api-server/subnets/',
                '/id/virtual-networks/',
                '/id/bgp/route-targets/',
                '/id/security-groups/id/',
                '/id/bgpaas/port/',
                '/vnc_api_server_obj_create/',
                'other_paths']

    def __init__(self, parser, old_json, new_json):
        self._logger = logging.getLogger(__name__)
        self._build_parser(parser)
        self.json_files = [ old_json, new_json ]
        self.diff = {}

    def _load_db(self):
        self.old_db = DB(json_file = self.json_files[0])
        self.new_db = DB(json_file = self.json_files[1])

    def _build_parser(self, parser):
        parser.add_argument(
            'new_json_file',
            help="json file to be compared")
        parser.add_argument(
            'scope',
            choices = self.KEYSPACES + ['zookeeper', 'object', 'all'],
            default = 'all',
            help="Scope to be compared")
        parser.add_argument(
            "objects", type=is_valid_uuid, nargs= '*',
            help="object uuid to be compared. ")
        parser.add_argument(
            "-d", "--detail", help="Display details",
            action='store_true', default=False)
        parser.set_defaults(func=self.db_compare)

    def db_compare(self, args):
        self._load_db()

        if args.scope == 'all':
            for s in self.KEYSPACES:
                self._compare_cassandra(s)
            self._compare_zk()
        elif args.scope == 'zookeeper':
            self._compare_zk()
        elif args.scope in self.KEYSPACES:
            self._compare_cassandra(args.scope)
        elif args.scope == 'object':
            if args.objects == []:
                # comparing config_db_uuid since no object id is provided
                self._compare_cassandra('config_db_uuid')
            else:
                self._compare_object(args.objects)

        self.print_diff(args.detail)

    def _compare_cassandra (self, ks):
        self.diff[ks] = {}
        old_ks = self.old_db.cassandra[ks]
        new_ks = self.new_db.cassandra[ks]

        old_cfs = set(old_ks.keys())
        new_cfs = set(old_ks.keys())

        if new_cfs != old_cfs:
            self._logger.warning('CFs were added/removed in %s', ks)
            self._logger.warning('\tAdded CF: %s',
                            str([ s for s in new_cfs - old_cfs]))
            self._logger.warning('\tRemoved CF: %s',
                            str([ s for s in old_cfs - new_cfs]))

        for cf in old_cfs.intersection(new_cfs):
            diff_cf = self.diff[ks][cf] = {'removed': {},
                                            'added': {},
                                            'modified': {}}
            if cf == 'obj_fq_name_table' or cf == 'obj_shared_table':
                for type, objs in old_ks[cf].items():
                    if type not in new_ks[cf]:
                        for name, value in objs.items():
                            diff_cf['removed'][type+':'+ name] = value
                    else:
                        (removed, added, modified) = \
                                dict_compare(objs, new_ks[cf][type])
                        for key, value in removed.items():
                            diff_cf['removed'][type+':'+key] = value
                        for key, value in added.items():
                            diff_cf['added'][type+':'+key] = value
                        for key, value in modified.items():
                            diff_cf['modified'][type+':'+key] = value

                for type, objs in new_ks[cf].items():
                    if type not in old_ks[cf]:
                        for name, value in objs.items():
                            diff_cf['added'][type+':'+ name] = value
            else:
                (diff_cf['removed'], diff_cf['added'], diff_cf['modified']) = \
                    dict_compare(old_ks[cf], new_ks[cf])

    def _compare_zk(self):
        diff_zk = self.diff['zookeeper'] = {}

        for type in self.ZK_PATHS:
            diff_zk[type] = {'removed': {},
                             'added': {},
                             'modified': {}}
        diff = {}
        (diff['removed'], diff['added'], diff['modified']) = \
            dict_compare(self.old_db.zk, self.new_db.zk)

        def zk_type(path):
            for type in self.ZK_PATHS:
                if path.startswith(type):
                    return type
            return 'others'

        for path, value in diff['removed'].items():
            diff_zk[zk_type(path)]['removed'][path] = value

        for path, value in diff['added'].items():
            diff_zk[zk_type(path)]['added'][path] = value

        for path, value in diff['modified'].items():
            diff_zk[zk_type(path)]['modified'][path] = value

    def _compare_object(self, obj_ids):
        old_db = self.old_db.cassandra
        new_db = self.new_db.cassandra
        for id in obj_ids:
            try:
                old_obj = old_db['config_db_uuid']['obj_uuid_table'][id]
                new_obj = new_db['config_db_uuid']['obj_uuid_table'][id]
            except KeyError:
                self._logger.warning('%s not found in obj_uuid_table', id)
                continue

            self.print_obj_diff(id, old_obj, new_obj)

    def print_diff(self, detail):
        indent = ' ' * 3
        for name, result in self.diff.items():
            if name in self.KEYSPACES:
                print "keyspace: %s" % (name)
            else:
                print "zookeeper:"
            for tbl_name, tbl in result.items():
                removed = 0 if 'removed' not in tbl else len(tbl['removed'])
                added = 0 if 'added' not in tbl else len(tbl['added'])
                modified = 0 if 'modified' not in tbl else len(tbl['modified'])
                print ("{}{:32}removed[-]: {:3}  added[+]: {:3}"
                       "  modified[*]: {:3}".format(
                       indent, tbl_name, removed, added, modified))
                if detail:
                    if removed:
                        for k in tbl['removed'].keys():
                            print "%s- %s" % (2*indent, str(k))
                    if added:
                        for k in tbl['added'].keys():
                            print "%s+ %s" % (2*indent, str(k))
                    if modified:
                        for k in tbl['modified'].keys():
                            print "%s* %s" % (2*indent, str(k))

    def print_obj_diff(self, uuid, old_obj, new_obj):
        indent = ' ' * 3
        print "%s:" % uuid
        for field in sorted(set(old_obj.keys()) | set(new_obj.keys())):
            if field in old_obj and field not in new_obj:
                mtime = ts_to_string(old_obj[field][1])
                DB_show.print_obj_field(field, old_obj[field][0],
                                        mtime=mtime,
                                        detail=True, prefix='-')
            elif field not in old_obj and field in new_obj:
                mtime = ts_to_string(new_obj[field][1])
                DB_show.print_obj_field(field, new_obj[field][0],
                                        mtime=mtime,
                                        detail=True, prefix='+')
            elif old_obj[field][0] != new_obj[field][0]:
                old_mtime = ts_to_string(old_obj[field][1])
                new_mtime = ts_to_string(new_obj[field][1])
                DB_show.print_obj_field(field, old_obj[field][0],
                                        mtime=old_mtime,
                                        detail=True, prefix='*[old]')
                DB_show.print_obj_field(field, new_obj[field][0],
                                        mtime=new_mtime,
                                        detail=True, prefix='*[new]')
            else:
                mtime = ts_to_string(old_obj[field][1])
                DB_show.print_obj_field(field, old_obj[field][0], detail=True)

class DB_show():
    """ Class to show objects in DB tables"""
    SUB_COMMAND = {
        'config_db': [{ 'fqn': 'obj_fq_name_table',
                        'uuid': 'obj_uuid_table',
                        'shared_obj': 'obj_shared_table'},
                      'config_db_uuid'],
        'useragent': [{'kv': 'useragent_keyval_table'},
                      'useragent'],
        'to_bgp': [{'sc_uuid': 'service_chain_uuid_table',
                     'sc_ip': 'service_chain_ip_address_table',
                     'sc': 'service_chain_table',
                     'rt': 'route_target_table'},
                   'to_bgp_keyspace'],
        'svc_mon': [{ 'si': 'service_instance_table',
                      'lb': 'loadbalancer_table',
                      'pl': 'pool_table'},
                    'svc_monitor_keyspace'],
        'dm': [{'pnf': 'dm_pnf_resource_table',
                'pr': 'dm_pr_vn_ip_table'},
               'dm_keyspace'],
        'zk': [{}, 'zookeeper']
    }
    # SUB_COMMAND = {
    #         'config_db': [ {'fqn': 'obj_fq_name_table',
    #                         'uuid': 'obj_uuid_table',
    #                         'shared_obj': 'obj_shared_table'},
    #                         'config_db_uuid'],
    #         'useragent': [{ 'kv': 'useragent_keyval_table'},
    #                         'useragent'],
    #         'to_bgp': [ {'sc_uuid': 'service_chain_uuid_table',
    #                         'sc_ip': 'service_chain_ip_address_table',
    #                         'sc': 'service_chain_table',
    #                         'rt': 'route_target_table'},
    #                         'to_bgp_keyspace'],
    #         'svc_mon': [ {
    #                         'si': 'service_instance_table',
    #                         'lb': 'loadbalancer_table',
    #                         'pl': 'pool_table'},
    #                         'svc_monitor_keyspace'],
    #         'dm': [ {
    #                         'pnf': 'dm_pnf_resource_table',
    #                         'pr': 'dm_pr_vn_ip_table'},
    #                         'dm_keyspace'],
    #         'zk': [ {}, 'zookeeper' ]
    # }

    OBJ_TYPES = [ 'service_appliance_set', 'virtual_router', 'security_group',
                  'global_system_config', 'network_policy', 'qos_config',
                  'route_table', 'interface_route_table', 'forwarding_class',
                  'service_appliance', 'routing_policy', 'network_ipam',
                  'config_node', 'namespace', 'logical_router', 'floating_ip',
                  'global_qos_config', 'service_health_check', 'bgp_router',
                  'domain', 'service_instance', 'loadbalancer_member',
                  'virtual_ip', 'api_access_list', 'qos_forwarding_class',
                  'discovery_service_assignment', 'project', 'route_target',
                  'virtual_machine', 'qos_queue', 'virtual_machine_interface',
                  'database_node', 'analytics_node', 'floating_ip_pool',
                  'instance_ip', 'access_control_list', 'bgp_as_a_service',
                  'global_vrouter_config', 'loadbalancer_pool', 'port_tuple',
                  'service_template', 'routing_instance', 'virtual_network']

    ZK_ENT_PROP = [ 'cZxid',
                    'mZxid',
                    'ctime',
                    'mtime',
                    'cversion',
                    'dataVersion',
                    'aclVersion',
                    'ephemeralOwner',
                    'dataLength',
                    'numChildren',
                    'pZxid']

    def __init__(self, parser, json_file):
        self._logger = logging.getLogger(__name__)
        self.db_json_file = json_file
        self._build_parser(parser)

    def _load_db(self):
        self.db = DB(json_file = self.db_json_file)

    def _build_parser(self, parser):
        sub_parsers = parser.add_subparsers()
        for cmd in self.SUB_COMMAND.keys():
            cmd_parser = sub_parsers.add_parser(cmd,
                help='Show %s' % self.SUB_COMMAND[cmd][1])
            if self.SUB_COMMAND[cmd][0]:
                cmd_parser.add_argument(
                    'table', choices=self.SUB_COMMAND[cmd][0].keys(),
                    help=str(self.SUB_COMMAND[cmd][0])
                )
            cmd_parser.add_argument(
                'search', nargs= '*',
                help="search string")
            cmd_parser.add_argument(
                '-t', '--time', action='store_true', default=False,
                help="Display creation/modification time")
            cmd_parser.add_argument(
                '-a', '--after', type=time_type,
                default = "2000-01-01T00:00:00",
                help = "show objects created after given time " +
                       "e.g. 2018-03-19T15:27:01")
            cmd_parser.add_argument(
                '-b', '--before', type=time_type,
                default = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
                help = "show objects created before given time " +
                       "e.g. 2018-03-19T15:27:01")
            cmd_parser.add_argument(
                '-d', '--detail', action='store_true', default=False,
                help="Display details")

            if cmd == 'config_db':
                cmd_parser.add_argument(
                    "--type", choices=self.OBJ_TYPES,
                    help="Object types")
                cmd_parser.add_argument(
                    "--re", type=str,
                    help="Regular expression to match object properties.")
                cmd_parser.add_argument(
                    '-i', '--include', nargs= '*', default=[],
                    help="Only display properties containing given string")
                cmd_parser.add_argument(
                    '-e', '--exclude', nargs= '*', default=[],
                    help="Exclude properties containing given string")

            if cmd == 'config_db' or cmd == 'zk':
                cmd_parser.set_defaults(func=getattr(self, 'show_' + cmd))
            else:
                cmd_parser.set_defaults(func=self.show_ks, ks=cmd)

    def show_config_db(self, args):
        assert (args.before > args.after), "Expect start_time < end_time"
        self._load_db()

        if args.table == 'fqn' or args.table == 'shared_obj':
            tbl_name = self.SUB_COMMAND['config_db'][0][args.table]
            # serach result in a set of tuple (mts, type, fqn, uuid, value)
            obj_set = self._search_fqn(tbl_name, args.search, args.type,
                    args.after, args.before)
            # print objects in order of modification timegm
            for obj in sorted(obj_set):
                time_str = '@' + ts_to_string(obj[0]) if args.time else ''
                print "%s type:%s fqn:%s uuid:%s value:%s" \
                        % (time_str, obj[1], obj[2], obj[3], obj[4])
        elif args.table == 'uuid':
            tbl_uuid = self.db.cassandra['config_db_uuid']['obj_uuid_table']
            obj_set = self._search_uuid(args.search, args.type,
                                        args.after, args.before, args.re)
            for u in sorted(obj_set):
                obj = tbl_uuid[u[1]]
                print ''
                self._print_obj(obj, u[1], args.time, args.detail,
                                args.include, args.exclude)

    def _search_fqn(self, tbl, search_key, obj_type='', start_utc_ts=0,
                    end_utc_ts=time.time()*1000000):
        tbl = self.db.cassandra['config_db_uuid'][tbl]
        obj_set = set()
        # list of [type, fqn, uuid, value, mts]
        type_list = [obj_type] if obj_type else tbl.keys()
        for type in type_list:
            for obj in tbl[type].keys():
                if not search_key or is_in(search_key, obj):
                    [value, mts] = tbl[type][obj]
                    if start_utc_ts < int(mts) < end_utc_ts:
                        obj_set.add((
                            mts,
                            type,
                            ':'.join(obj.split(':')[:-1]),
                            obj.split(':')[-1],
                            value))
        return obj_set

    def _search_uuid( self, search_key, obj_type='', start_utc_ts=0,
                      end_utc_ts=time.time()*1000000, regex=''):
        tbl = self.db.cassandra['config_db_uuid']['obj_uuid_table']
        logger = self._logger
        if regex:
            re_obj = re.compile(regex)

        uuid_set = set()
        if not search_key:
            for o in tbl.keys():
                #print "processing uuid %s" % o
                try:
                    (mts, type) = (
                            self._get_uuid_time(tbl[o]),
                            tbl[o]['type'][0].strip('"'))
                except:
                    logger.warning('%s is missing mandatory fields', o)
                    if logger.level < logging.ERROR:
                        self._print_obj(tbl[o], o)
                    continue
                else:
                    if (start_utc_ts < mts[1] < end_utc_ts and
                        not obj_type or type == obj_type):
                        if regex:
                            for k in tbl[o].keys():
                                if re_obj.match(k):
                                    uuid_set.add((mts, o))
                        else:
                            uuid_set.add((mts, o))
        else:
            for s in search_key:
                if is_valid_uuid(s):
                    try:
                        obj = tbl[s]
                    except KeyError:
                        logger.warning('%s not found in obj_uuid_table', s)
                        continue
                    else:
                        # If search key is uuid, ignore checking object type
                        # and search regex.
                        try:
                            mts = self._get_uuid_time(tbl[s])[1]
                        except:
                            logger.warning('%s is missing mandatory fields', s)
                            if logger.level < logging.ERROR:
                                self._print_obj(tbl[s], s, offset='   ')
                            continue
                        else:
                            if start_utc_ts < mts < end_utc_ts:
                                uuid_set.add((mts, s))
                else:
                    for o in tbl.keys():
                        try:
                            (fqn, mts, type) = (
                                tbl[o]['fq_name'][0],
                                self._get_uuid_time(tbl[o]),
                                tbl[o]['type'][0].strip('"'))
                        except:
                            logger.warning('%s is missing mandatory fields', o)
                            if logger.level < logging.ERROR:
                                self._print_obj(tbl[o], o, offset='   ')
                            continue
                        else:
                            if ((s in fqn or s in o) and
                                (start_utc_ts < mts[1] < end_utc_ts) and
                                (not obj_type or obj_type == type)):
                                uuid_set.add((mts, o))
        return uuid_set

    def show_ks(self, args):
        # this method handles requests for all table except the config_db_uuid
        # and zookeeper
        self._load_db()

        ks_name = self.SUB_COMMAND[args.ks][1]
        tbl_name = self.SUB_COMMAND[args.ks][0][args.table]
        tbl = self.db.cassandra[ks_name][tbl_name]

        assert (args.before > args.after), "Expect start_time < end_time"

        obj_set = set()
        for key in tbl.keys():
            # apply search only on key but not data
            if args.search and not is_in(args.search, key):
                continue
            # Each field has a modification/creation timeselfself.
            # Use the first the first property time as the object time
            prop = tbl[key].keys()[0]
            mts = int(tbl[key][prop][1])
            # apply before and after time contraints
            if args.after < mts < args.before:
                obj_set.add((mts, key))

        if len(obj_set):
            # print matched objects
            for obj in sorted(obj_set):
                self._print_obj(tbl[obj[1]], obj[1], args.time, args.detail)
        else:
            self._logger.info('No matched object is found')

    def show_zk(self, args):
        self._load_db()
        tbl = self.db.zk

        # timestamp in zk is msec
        start_time_ts = args.after/1000
        end_time_ts = args.before/1000
        assert (end_time_ts > start_time_ts), "Expect start_time < end_time"

        obj_set = set()
        for key in tbl.keys():
            if args.search and not is_in(args.search, key):
                continue
            mts = int(tbl[key][1][2])
            if start_time_ts < mts < end_time_ts:
                obj_set.add((mts, key))

        if len(obj_set):
            # sort the objects based on modification time
            for obj in sorted(obj_set):
                self._print_zk(tbl[obj[1]], obj[1], args.time, args.detail)
        else:
            self._logger.info('No matched object is found')

    def _get_uuid_time(self, obj):
        fmt = "%Y-%m-%dT%H:%M:%S.%f"
        id_perms = json.loads(obj['prop:id_perms'][0])
        last_modified = time_type(id_perms['last_modified'], fmt)
        created = time_type(id_perms['created'], fmt)
        return (created, last_modified)

    def _print_obj( self, obj, display_name, show_time=False, detail=False,
                    in_list=[], ex_list=[], offset=''):
        indent = ' ' * 3
        assert isinstance(obj, dict), "expect obj repsented as a dictionary"
        print "%s%s:" % (offset, display_name)

        for field in sorted(obj.iterkeys()):
            # apply include/exclude filters
            if len(in_list) and not is_in(in_list, field):
                continue
            if len(ex_list) and is_in(ex_list, field):
                continue
            # dict value must be a list [data, timestamp]
            value = obj[field]
            assert isinstance(value, list), \
                   "expect a list for %s %s field" % (id, field)
            mtime = '@' + ts_to_string(value[1]) if show_time else ''

            self.print_obj_field(field, value[0], mtime, detail=detail)

    @staticmethod
    def print_obj_field(field, value, mtime='', offset='',
                        detail=False, prefix=''):
        indent = ' ' * 3
        field_str = offset + indent + prefix + mtime + ' ' + field
        if field == 'fq_name' or isinstance(value, list):
            print ("%s: %s" %
                   (field_str, ':'.join(str(e) for e in json.loads(value))))
        elif isjson(value) or isinstance(value, dict):
            print "%s: ..." % (field_str)
            if detail:
                pretty_print(value, offset + 2 * indent)
        else:
            print "%s: %s" % (field_str, value.strip('"'))


    def _print_zk (self, entry, display_name, show_time=False,
                  detail=False, offset=''):
        indent = ' ' * 3
        mtime = '@' + ts_to_string(entry[1][2]) if show_time else ''

        print "%s%s%s" % (offset, mtime, display_name)
        # make special handling for subnets entres
        # convert dec to ip addresses
        if display_name.startswith('/api-server/subnets'):
            [prefix, mask_len, addr] = display_name.split('/')[-4:-1]
            if is_number(mask_len) and int(mask_len) < 129:
                if (int(mask_len) > 32 or '::' == prefix[-2:]):
                    print ("%sAddr: %s" %
                           (offset + indent, dec_to_ip(long(addr), 6)))
                else:
                    print ("%sAddr: %s" %
                           (offset + indent, dec_to_ip(long(addr), 4)))

        print "%s%s" % (offset + indent, str(entry[0]))

        if detail:
            # assume the entry data order is the same as ZK_ENT_PROP
            for e in [a + ' = ' + str(b)
                      for a, b in zip(self.ZK_ENT_PROP, entry[1])]:
                print "%s%s" % (offset+indent, e)

def main():
    parser = argparse.ArgumentParser(
        prog='db_explorer',
        description='Script to explore/compare Contrail configuration in json')
    parser.add_argument(
        '--version', action='version', version='%(prog)s ' + VERSION)
    parser.add_argument(
        '--json_file', default = 'db-dump.json', metavar='FILE',
        help="Json file generated by db_json_exmin.py, default='%(default)s'")
    parser.add_argument(
        '-v', '--verbose', action='store_true', default=False,
        help="Run in verbose/INFO mode, default False")


    argv = sys.argv[1:]
    # stream=sys.stdout is to make all output to stdout instead of stderr
    logging.basicConfig(stream=sys.stdout, level=logging.ERROR,
                        format='%(levelname)-8s %(message)s')
    logger = logging.getLogger(__name__)
    try:
        argv.index('--verbose')
    except ValueError:
        logger.setLevel(logging.ERROR)
    else:
        logger.setLevel(logging.INFO)

    json_file = os.environ.get('DB_JSON_FILE', 'db-dump.json')
    try:
        json_file = argv[argv.index('--json_file') + 1]
    except ValueError:
        pass

    try:
        new_json_file = argv[argv.index('comp') + 1]
    except ValueError:
        new_json_file = None

    oper_parsers = parser.add_subparsers()
    show_parser = oper_parsers.add_parser('show', help='db show')
    DB_show(show_parser, json_file)
    comp_parser = oper_parsers.add_parser('comp', help='db compare')
    DB_comp(comp_parser, json_file, new_json_file)

    try:
        import argcomplete
        argcomplete.autocomplete(parser)
    except:
        logging.info('Failed to load argcomplete')
        logging.info('Command autocomplete is disabled')

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
