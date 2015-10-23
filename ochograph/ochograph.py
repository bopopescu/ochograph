#
# This little tool will draw a dependency graph of your Ochopod cluster(s), 
# showing what pods depend on what other pods.
#
# The idea is that this can be useful for documenting your systems infrastructure. 
#
# The tool will first get the list of all pods from Zookeper and then  
# get the necessary details for each pod by leveraging its REST API.
#
# For this tool to work, every single pod that has dependencies (i.e. the 
# 'depends_on' variable of the Reactive class is not empty) must list its
# dependencies as a list of "<cluster_name>:<port>" under the 'dependsOn' key, e.g.:
#
#        def sanity_check(self, pid):
#            # Will result in something like "mysql:3306"
#            depends_on = cfg['db_cluster_name'] + ":" + cfg['db_cluster_port']
#
#            # Result would then be {'dependsOn': ["mysql:3306"]}
#            return {'dependsOn': [depends_on]}
#
import networkx as nx
import re
import json
import fnmatch
import logging
import requests
import time
import sys

from logging import DEBUG, Formatter
from logging.handlers import RotatingFileHandler
from kazoo.client import KazooClient
from threading import Thread
from requests.exceptions import Timeout as HTTPTimeout


logLevel = DEBUG

logger = logging.getLogger()
handler = RotatingFileHandler("ochograph.log", maxBytes=32764, backupCount=3)
handler.setLevel(logLevel)
handler.setFormatter(Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logLevel)


# Lookup all pods registered in Zookeeper.
def lookup_pods(zk_hosts, regex, subset=None):
    zk = KazooClient(hosts=zk_hosts)
    zk.start()

    ROOT = '/ochopod/clusters'

    pods = {}

    clusters = [cluster for cluster in zk.get_children(ROOT) if fnmatch.fnmatch(cluster, regex)]
    for cluster in clusters:
        kids = zk.get_children('%s/%s/pods' % (ROOT, cluster))
        for kid in kids:
            js, _ = zk.get('%s/%s/pods/%s' % (ROOT, cluster, kid))
            hints = \
                {
                    'id': kid,
                    'cluster': cluster
                }
            #
            # - the number displayed by the tools (e.g shared.docker-proxy #4) is that monotonic integer
            #   derived from zookeeper
            #
            hints.update(json.loads(js))
            seq = hints['seq']
            if not subset or seq in subset:
                pods['%s #%d' % (cluster, seq)] = hints

    zk.stop()
    return pods


# Used to get the details of a given pod by hitting its API directly. 
# Copied from Ochothon itself.
class _Post(Thread):
    """
    We optimize a bit the HTTP queries to the pods by running them on separate threads (this can be a
    tad slow otherwise for more than 10 queries in a row)
    """

    def __init__(self, key, hints, command, timeout=10.0, js=None):
        super(_Post, self).__init__()

        self.key = key
        self.hints = hints
        self.command = command
        self.timeout = timeout
        self.js = js
        self.body = None
        self.code = None

        self.start()

    def run(self):

        url = 'N/A'
        try:
            ts = time.time()
            port = self.hints['port']
            assert port in self.hints['ports'], 'ochopod control port not exposed @ %s (user error ?)' % self.key
            url = 'http://%s:%d/%s' % (self.hints['ip'], self.hints['ports'][port], self.command)
            reply = requests.post(url, timeout=self.timeout, data=self.js)
            self.body = reply.json()
            self.code = reply.status_code
            ms = 1000 * (time.time() - ts)
            logger.debug('-> %s (HTTP %d, %s ms)' % (url, reply.status_code, int(ms)))

        except HTTPTimeout:
            logger.debug('-> %s (timeout)' % url)

        except Exception as failure:
            logger.debug('-> %s (i/o error, %s)' % (url, failure))

    def join(self, timeout=None):

        Thread.join(self)
        return self.key, self.hints['seq'], self.body, self.code



####################################################################################################################################
zk_hosts = None

print "\nOchograph"
print "=========\n"

if sys.argv:
    arg_index = 0
    for arg in sys.argv:
        if arg == '-z' or arg == '--zookeeper':
            try:
                zk_hosts = sys.argv[arg_index + 1]
            except:
                pass
        arg_index += 1

if not zk_hosts:
    try:
        with open('/etc/mesos/zk', 'r') as f:
            content = f.readlines()[0]
            zk_hosts = content[5:-7]
    except:
        logger.warning("Could not guess Zookeeper host and port")

if not zk_hosts:
    print "Could not guess Zookeeper host(s), please specify one (e.g. pythong ochograph.py -z 127.0.0.1:2181)"
    sys.exit(0)

print "Using Zookeeper host(s): %s" % zk_hosts
print ""



# For testing (since no access to Zookeeper)
#outDict = {u'dev.cr-app #31': (31, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochopod.dev.cr-app-2015-10-22-12-17-27.a42b56ac-78b7-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.122', u'public': u'', u'ports': {u'8085': 31213, u'8080': 31212}, u'metrics': {u'info': {u'leveraging': {u'penalties': {u'file': u'penaltiesDisabled.conf', u'noPenaltyPerfectMatches': True, u'penaltiesLines': [u'*.*.*.*.* -> *.*.*.*.* = 0', u'*.*.*.* -> *.*.*.* = 0', u'*.*.* -> *.*.* = 0', u'*.* -> *.* = 0', u'* -> * = 0']}, u'fuzzyMatching': {u'maxNbNgramMatches': 20000, u'nbNgramMatchesTolerance': 2}}, u'authenticationEnabled': True, u'leader': True}, u'uptime': u'18.67 hours (pid 334)'}, u'application': u'ochopod.dev.cr-app-2015-10-22-12-17-27', u'state': u'leader', u'port': u'8080'}, 200),
#           u'dev.cr-app #34': (31, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochopod.dev.cr-app-2015-10-22-12-17-23.a42b56ac-78b7-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.123', u'public': u'', u'ports': {u'8085': 32214, u'8080': 32545}, u'metrics': {u'info': {u'leveraging': {u'penalties': {u'file': u'penaltiesDisabled.conf', u'noPenaltyPerfectMatches': True, u'penaltiesLines': [u'*.*.*.*.* -> *.*.*.*.* = 0', u'*.*.*.* -> *.*.*.* = 0', u'*.*.* -> *.*.* = 0', u'*.* -> *.* = 0', u'* -> * = 0']}, u'fuzzyMatching': {u'maxNbNgramMatches': 20000, u'nbNgramMatchesTolerance': 2}}, u'authenticationEnabled': True, u'leader': False}, u'uptime': u'11.52 hours (pid 214)'}, u'application': u'ochopod.dev.cr-app-2015-10-22-12-17-28', u'state': u'leader', u'port': u'8080'}, 200),
#           u'dev.ls-reverse-proxy #4': (4, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochopod.dev.ls-reverse-proxy-2015-10-23-06-48-37.17f0951d-7952-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.122', u'public': u'', u'ports': {u'80': 80, u'8080': 31614}, u'metrics': {u'uptime': u'0.15 hours (pid 2053)', u'dependsOn': [u'*-frontend:80']}, u'application': u'ochopod.dev.ls-reverse-proxy-2015-10-23-06-48-37', u'state': u'leader', u'port': u'8080'}, 200),
#           u'marathon.portal #63': (63, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochothon.1360ce5e-6789-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.122', u'public': u'', u'ports': {u'8080': 31117, u'9000': 9000}, u'metrics': {u'uptime': u'543.47 hours (pid 48)'}, u'application': u'ochothon', u'state': u'leader', u'port': u'8080'}, 200),
#           u'dev.cr-frontend #8': (8, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochopod.dev.cr-frontend-2015-10-23-06-56-45.3a734f5e-7953-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.122', u'public': u'', u'ports': {u'80': 31497, u'8080': 31496}, u'metrics': {u'uptime': u'0.15 hours (pid 49)', u'dependsOn': [u'cr-app:8085']}, u'application': u'ochopod.dev.cr-frontend-2015-10-23-06-56-45', u'state': u'leader', u'port': u'8080'}, 200)}

pods = lookup_pods(zk_hosts, "*", None)
threads = [_Post(pod, hints, "info") for pod, hints in pods.items()]
out = [thread.join() for thread in threads]
outDict = {key: (seq, body, code) for (key, seq, body, code) in out if code}


# Returns a list of pods_data, i.e list of ('<pod_name>', '<namespace>', <seq>, '[<depends_on>]')
def find_dependencies(pods_dict, depends_on, namespace):
    if not depends_on:
        return None
    else:
        result = []
        for pod_key in outDict.keys():
            pod_data = get_pod_data(pod_key, pods_dict.get(pod_key))
            #print "\n\nPod key: %s: " % pod_key
            #print pod_data
            #print pods_dict.get(pod_key)
            # Same namespace
            if pod_data[1] == namespace:
                logger.debug("Same namespaces (%s)" % namespace)
                for dep in depends_on:
                    dep_re = dep[0].replace("*", ".*")
                    #print dep_re
                    if re.match(dep_re, pod_data[0]):
                        result.append(pod_data)
                    else:
                        logger.debug('No match: %s vs %s' % (dep_re, pod_data[0]))
            else:
                logger.debug("Different namespaces %s vs %s" % (pod_data[1], namespace))
            if pod_key.find(namespace + ".") == 0:
                logger.debug("Matching workspace for %s" % pod_key)
                #print pod_key
            else:
                logger.debug("Not the right namespace for %s" % pod_key)
        if len(result) > 0:
            return result
        else:
            return None

# Returns a tuple of the form ('<pod_name>', '<namespace>', <seq>, '[<depends_on>]', '<pod_id>')
def get_pod_data(pod_id, value):
    hash_pos = pod_id.find("#")
    pod_with_namespace = pod_id[0:hash_pos-1]
    dot_pos_sum = 0
    tmp = pod_with_namespace
    while tmp.find('.') >= 0:
        dot_pos = tmp.find('.')
        dot_pos_sum += dot_pos + 1
        tmp = tmp[dot_pos+1:]

    namespace = pod_with_namespace[0:dot_pos_sum-1]
    pod = pod_with_namespace[dot_pos_sum:]
    seq = pod_id[hash_pos+1:]

    depends_on = None
    body = value[1]
    if body.has_key('metrics') and body['metrics'].has_key('dependsOn'):
        depends_on_original = body['metrics']['dependsOn']
        depends_on = []
        for one_dep in depends_on_original:
            depends_on.append(one_dep.split(":"))

    seq =  value[0]

    return (pod, namespace, seq, depends_on, pod_id)



key_with_deps = {}
for key in outDict.keys():

    pod_data = get_pod_data(key, outDict.get(key))

    deps = find_dependencies(outDict, pod_data[3], pod_data[1])

    key_with_deps[key] = deps

    logger.debug("Pod: %s, deps: %s" % (key, deps))


# Return the list of pod IDs that depend on a given pod.
def get_depends_on_me(pod_id, key_with_deps):
    result = []
    for kd in key_with_deps.keys():
        deps = key_with_deps.get(kd)
        if deps:
            for dep in deps:
                if dep[4] == pod_id:
                    result.append(kd)
    if len(result) > 0:
        return result
    else:
        return None


# Generate the graph.
G = nx.DiGraph()
graph_roots = set()
for kd in key_with_deps.keys():
    depends_on_me = get_depends_on_me(kd, key_with_deps)
    logger.debug("Pod ID %s has the following depending on it: %s" % (kd, depends_on_me))
    if not depends_on_me:
        graph_roots.add(kd)
    else:
        for dom in depends_on_me:
            G.add_edge(dom, kd)


# Display the graph.
for s in graph_roots:
    print s
    spacer = {s: 0}
    try:
        for prereq, target in nx.dfs_edges(G, s):
            spacer[target] = spacer[prereq] + 2
            print '{spacer}+-{t}'.format(
                                         spacer=' ' * spacer[prereq],
                                         t=target)
    # Handles the case where a pod has no dependency.
    except KeyError:
        pass
    print ''
