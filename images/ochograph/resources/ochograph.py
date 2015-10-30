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
# dependencies as a list of "<cluster_name>" under the 'dependsOn' key of
# the JSON returned by the sanity_check method, e.g.:
#
#        def sanity_check(self, pid):
#            # Will result in something like "mysql"
#            depends_on = cfg['db_cluster_name']
#
#            # Result would then be {'dependsOn': ["mysql"]}
#            return {'dependsOn': [depends_on]}
#
import networkx as nx
import json
import fnmatch
import logging
import requests
import time
import sys
import string
import random
import os

from logging import DEBUG, Formatter
from logging.handlers import RotatingFileHandler
from kazoo.client import KazooClient
from threading import Thread
from requests.exceptions import Timeout as HTTPTimeout


ROOT_NODE = "ROOT" 
LOG_FILE = "ochograph.log"
LOG_LEVEL = DEBUG

logger = logging.getLogger()
# 1048576 Bytes = 1 MB 
handler = RotatingFileHandler(LOG_FILE, maxBytes=1048576, backupCount=3)
handler.setLevel(LOG_LEVEL)
handler.setFormatter(Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(LOG_LEVEL)

   

    
# See http://stackoverflow.com/questions/287871/print-in-terminal-with-colors-using-python
class bcolors:
    #HEADER = '\033[95m'
    #OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    #WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    #BOLD = '\033[1m'
    #UNDERLINE = '\033[4m'
    
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
            logger.debug('Response payload: %s' % self.body)

        except HTTPTimeout:
            logger.debug('-> %s (timeout)' % url)

        except Exception as failure:
            logger.debug('-> %s (i/o error, %s)' % (url, failure))

    def join(self, timeout=None):

        Thread.join(self)
        return self.key, self.hints['seq'], self.body, self.code



# Returns a list of pods_data, i.e list of ('<pod_name>', '<namespace>', <seq>, ['<depends_on_ip>'], '<pod_id>', ['<ports>'])
def find_dependencies(pods_dict, depends_on, namespace):
    if not depends_on:
        return None
    else:
        result = []
        for pod_key in pods_dict.keys():
            pod_data = get_pod_data(pod_key, pods_dict.get(pod_key))            
            
            for dep in depends_on:
                where = None
                # Absolute dependency.
                if dep.startswith("/"):
                    where = dep[1:] 
                else:
                    where = namespace + "." + dep
                    
                pod_path = pod_data[1] + "." + pod_data[0]
                if "*" in where and fnmatch.fnmatch(pod_path, where):
                    result.append(pod_data)
                elif "*" not in where and pod_path == where:
                    result.append(pod_data)
                        
        if len(result) > 0:
            return result
        else:
            return None

# Returns a tuple of the form ('<pod_name>', '<namespace>', <seq>, ['<depends_on_ip>'], '<pod_id>', ['<ports>'])
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
    #seq = pod_id[hash_pos+1:]
    

    depends_on = None
    body = value[1]
    if body.has_key('dependsOn'):
        depends_on = body['dependsOn']
    elif body.has_key('metrics') and body['metrics'].has_key('dependsOn'):
        depends_on = body['metrics']['dependsOn']
        
    seq =  value[0]
    
    ports = None
    if body.has_key('ports') and body['ports'].keys():
        ports = body['ports'].keys()
        

    return (pod, namespace, seq, depends_on, pod_id, ports)

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
    
def get_pods_details(is_local, output, hide_zookeeper_info):
    if is_local:
        # For testing (since no access to Zookeeper)
        pods_details = {u'dev.cr-app #31': (31, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochopod.dev.cr-app-2015-10-22-12-17-27.a42b56ac-78b7-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.122', u'public': u'', u'ports': {u'8085': 31213, u'8080': 31212}, u'metrics': {u'info': {u'leveraging': {u'penalties': {u'file': u'penaltiesDisabled.conf', u'noPenaltyPerfectMatches': True, u'penaltiesLines': [u'*.*.*.*.* -> *.*.*.*.* = 0', u'*.*.*.* -> *.*.*.* = 0', u'*.*.* -> *.*.* = 0', u'*.* -> *.* = 0', u'* -> * = 0']}, u'fuzzyMatching': {u'maxNbNgramMatches': 20000, u'nbNgramMatchesTolerance': 2}}, u'authenticationEnabled': True, u'leader': True}, u'uptime': u'18.67 hours (pid 334)'}, u'application': u'ochopod.dev.cr-app-2015-10-22-12-17-27', u'state': u'leader', u'port': u'8080', u'dependsOn': [u'/other.db']}, 200),
                   u'dev.cr-app #34': (34, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochopod.dev.cr-app-2015-10-22-12-17-23.a42b56ac-78b7-11e5-b252-065c340003c5', u'process': u'stopped', u'ip': u'10.41.91.123', u'public': u'', u'ports': {u'8085': 32214, u'8080': 32545}, u'metrics': {u'info': {u'leveraging': {u'penalties': {u'file': u'penaltiesDisabled.conf', u'noPenaltyPerfectMatches': True, u'penaltiesLines': [u'*.*.*.*.* -> *.*.*.*.* = 0', u'*.*.*.* -> *.*.*.* = 0', u'*.*.* -> *.*.* = 0', u'*.* -> *.* = 0', u'* -> * = 0']}, u'fuzzyMatching': {u'maxNbNgramMatches': 20000, u'nbNgramMatchesTolerance': 2}}, u'authenticationEnabled': True, u'leader': False}, u'uptime': u'11.52 hours (pid 214)'}, u'application': u'ochopod.dev.cr-app-2015-10-22-12-17-28', u'state': u'leader', u'port': u'8080', u'dependsOn': []}, 200),
                   # Uncomment this one to test circular dependencies.
                   #u'dev.cr-app #35': (35, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochopod.dev.cr-app-2015-10-22-12-17-23.a42b56ac-78b7-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.123', u'public': u'', u'ports': {u'8085': 32214, u'8080': 32545}, u'metrics': {u'info': {u'leveraging': {u'penalties': {u'file': u'penaltiesDisabled.conf', u'noPenaltyPerfectMatches': True, u'penaltiesLines': [u'*.*.*.*.* -> *.*.*.*.* = 0', u'*.*.*.* -> *.*.*.* = 0', u'*.*.* -> *.*.* = 0', u'*.* -> *.* = 0', u'* -> * = 0']}, u'fuzzyMatching': {u'maxNbNgramMatches': 20000, u'nbNgramMatchesTolerance': 2}}, u'authenticationEnabled': True, u'leader': False}, u'uptime': u'11.52 hours (pid 214)'}, u'application': u'ochopod.dev.cr-app-2015-10-22-12-17-28', u'state': u'leader', u'port': u'8080', u'dependsOn': [u'cr-frontend']}, 200),
                   u'dev.ls-reverse-proxy #4': (4, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochopod.dev.ls-reverse-proxy-2015-10-23-06-48-37.17f0951d-7952-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.122', u'public': u'', u'ports': {u'80': 80, u'8080': 31614}, u'metrics': {u'uptime': u'0.15 hours (pid 2053)'}, u'application': u'ochopod.dev.ls-reverse-proxy-2015-10-23-06-48-37', u'state': u'leader', u'port': u'8080', u'dependsOn': [u'*-frontend']}, 200),
                   # This one is not a dependency of cr-frontend because of a different port.
                   u'dev.cr-app #36': (36, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochopod.dev.cr-app-2015-10-22-12-17-75.a42b56ac-78b7-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.145', u'public': u'', u'ports': {u'8086': 32214, u'8080': 32545}, u'metrics': {u'info': {u'leveraging': {u'penalties': {u'file': u'penaltiesDisabled.conf', u'noPenaltyPerfectMatches': True, u'penaltiesLines': [u'*.*.*.*.* -> *.*.*.*.* = 0', u'*.*.*.* -> *.*.*.* = 0', u'*.*.* -> *.*.* = 0', u'*.* -> *.* = 0', u'* -> * = 0']}, u'fuzzyMatching': {u'maxNbNgramMatches': 20000, u'nbNgramMatchesTolerance': 2}}, u'authenticationEnabled': True, u'leader': False}, u'uptime': u'11.52 hours (pid 214)'}, u'application': u'ochopod.dev.cr-app-2015-10-22-12-17-28', u'state': u'leader', u'port': u'8080', u'dependsOn': []}, 200),
                   
                   # Uncomment this one to test checking pods that do not expose their dependencies.
                   #u'dev.cr-app #54': (54, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochopod.dev.cr-app-2015-10-22-12-17-75.a42b56ac-78b7-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.145', u'public': u'', u'ports': {u'8086': 32214, u'8080': 32545}, u'metrics': {u'info': {u'leveraging': {u'penalties': {u'file': u'penaltiesDisabled.conf', u'noPenaltyPerfectMatches': True, u'penaltiesLines': [u'*.*.*.*.* -> *.*.*.*.* = 0', u'*.*.*.* -> *.*.*.* = 0', u'*.*.* -> *.*.* = 0', u'*.* -> *.* = 0', u'* -> * = 0']}, u'fuzzyMatching': {u'maxNbNgramMatches': 20000, u'nbNgramMatchesTolerance': 2}}, u'authenticationEnabled': True, u'leader': False}, u'uptime': u'11.52 hours (pid 214)'}, u'application': u'ochopod.dev.cr-app-2015-10-22-12-17-28', u'state': u'leader', u'port': u'8080'}, 200),
                   
                   u'marathon.portal #63': (63, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochothon.1360ce5e-6789-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.122', u'public': u'', u'ports': {u'8080': 31117, u'9000': 9000}, u'metrics': {u'uptime': u'543.47 hours (pid 48)'}, u'application': u'ochothon', u'state': u'leader', u'port': u'8080', u'dependsOn': []}, 200),
                   u'other.db #88': (88, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochothon.1360ce5e-6789-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.122', u'public': u'', u'ports': {u'8080': 31117, u'9000': 9000}, u'metrics': {u'uptime': u'543.47 hours (pid 48)'}, u'application': u'ochothon', u'state': u'leader', u'port': u'8080', u'dependsOn': []}, 200),
                   u'dev.cr-frontend #8': (8, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochopod.dev.cr-frontend-2015-10-23-06-56-45.3a734f5e-7953-11e5-b252-065c340003c5', u'process': u'running', u'ip': u'10.41.91.122', u'public': u'', u'ports': {u'80': 31497, u'8080': 31496}, u'metrics': {u'uptime': u'0.15 hours (pid 49)'}, u'application': u'ochopod.dev.cr-frontend-2015-10-23-06-56-45', u'state': u'leader', u'port': u'8080', u'dependsOn': [u'cr-app']}, 200),
                   u'dev.cr-frontend #9': (9, {u'node': u'patwstmesosdev2.ecs.ads.autodesk.com', u'status': u'', u'task': u'ochopod.dev.cr-frontend-2015-10-23-06-56-45.3a734f5e-7953-11e5-b252-065c340003c6', u'process': u'running', u'ip': u'10.41.91.123', u'public': u'', u'ports': {u'80': 31499, u'8080': 31500}, u'metrics': {u'uptime': u'0.15 hours (pid 49)', u'dependsOn': [u'cr-app']}, u'application': u'ochopod.dev.cr-frontend-2015-10-23-06-56-46', u'state': u'leader', u'port': u'8080'}, 200)}
    
        return pods_details, output
    else:
        if not zk_hosts:
            output += "Could not guess Zookeeper host(s), please specify one (e.g. pythong ochograph.py -z 127.0.0.1:2181)\n"
            return None, output
            
        else:
        
            if not hide_zookeeper_info:
                output += "Using Zookeeper host(s): %s" % zk_hosts
                output += "\n\n"
        
            pods = lookup_pods(zk_hosts, "*", None)
            threads = [_Post(pod, hints, "info") for pod, hints in pods.items()]
            out = [thread.join() for thread in threads]
            pods_details = {key: (seq, body, code) for (key, seq, body, code) in out if code}
            
            return pods_details, output
            
                   
    

def get_graph(pods_details):
    key_with_deps = {}
    for key in pods_details.keys():
    
        pod_data = get_pod_data(key, pods_details.get(key))
    
        deps = find_dependencies(pods_details, pod_data[3], pod_data[1])
    
        key_with_deps[key] = deps
    
        logger.debug("Pod: %s, deps: %s" % (key, deps))
        
    # Generate the graph.
    G = nx.DiGraph()
    for kd in key_with_deps.keys():
        depends_on_me = get_depends_on_me(kd, key_with_deps)
        logger.debug("Pod ID %s has the following depending on it: %s" % (kd, depends_on_me))
        if not depends_on_me:
            G.add_edge(ROOT_NODE, kd)
        else:
            for dom in depends_on_me:
                logging.debug("Adding edge (%s, %s)" % (dom, kd))
                G.add_edge(dom, kd)
    
    return G

# Returns a tuple where the first element is a list of running pod IDs and the second
# element a list of non-running pod IDs 
def get_nodes_status(graph, pods_details):
    def is_process_running(pod_id):
        if pods_details.has_key(pod_id):
            body = pods_details.get(pod_id)[1]
            if body.has_key("process"):
                return "running" == body.get("process")
        return False
    
    ok_nodes = []
    ko_nodes = []
    
    for node in nx.nodes(graph):
        if node != ROOT_NODE:
            if is_process_running(node):
                ok_nodes.append(node)
            else:
                ko_nodes.append(node)
                
    return ok_nodes, ko_nodes

def get_no_depends_on(pods_details):
    result = []
    for pod_id in pods_details.keys():
        pod_data = get_pod_data(pod_id, pods_details.get(pod_id))
        if pod_data[3] == None:
            result.append(pod_id)
    return result

def draw_image(graph, ok_nodes, ko_nodes, image_file):
    draw_image_graphviz(graph, ok_nodes, ko_nodes, image_file)
    
def draw_image_graphviz(graph, ok_nodes, ko_nodes, image_file):
    A = nx.to_agraph(graph)
    
    for ok_node in ok_nodes:
        n = A.get_node(ok_node)
        n.attr['style']='filled'
        n.attr['fillcolor']="#00AA00"
        
    for ko_node in ko_nodes:
        n = A.get_node(ko_node)
        n.attr['style']='filled'
        n.attr['fillcolor']="#FF5555"
       
    # Hide root node.
    root_node = A.get_node(ROOT_NODE)
    root_edges = A.edges(nbunch=root_node) 
    for root_edge in root_edges:
        A.remove_edge(root_edge)
    A.remove_node(root_node)
        
    A.layout('dot', args='-Nfontsize=10 -Nwidth=".2" -Nheight=".2" -Nmargin=0 -Gfontsize=8')
    A.draw(image_file)

def draw_image_matplotlib(graph, ok_nodes, ko_nodes, image_file):
    # Only try an import matplotlib if on want to output an image, but still
    # let the program crash if it is not there (i.e no try/except)
    import matplotlib
    # Force matplotlib to not use any Xwindows backend.
    matplotlib.use('Agg')                
    import matplotlib.pyplot as plt

    # See http://stackoverflow.com/questions/11479624/is-there-a-way-to-guarantee-hierarchical-output-from-networkx
    pos = nx.graphviz_layout(graph, prog='dot')
    
    plt.axis('off')
    
    nx.draw_networkx_nodes(graph, pos,
           nodelist=ok_nodes,
           node_color='g',
           node_size=400,
           alpha=1.0)
    
    nx.draw_networkx_nodes(graph,pos,
           nodelist=ko_nodes,
           node_color='r',
           node_size=400,
           alpha=1.0)
    
    labels={}
    for n in ok_nodes + ko_nodes:
        labels[n] = n
        
    # Because we want the label below the node.
    labels_pos = {}
    for node in pos.keys():
        v = pos.get(node)
        labels_pos[node] = (v[0], v[1])
    
    nx.draw_networkx_labels(graph, labels_pos, labels, font_size=7)
    
    # Because we do not want to draw edges to the root node.
    edges = []
    for edge in graph.edges():
        if edge[0] != ROOT_NODE and edge[1] != ROOT_NODE:
            edges.append(edge)
            
    nx.draw_networkx_edges(graph, pos, width=1.0, alpha=0.3, arrows=False, edgelist=edges)
    
    plt.savefig(image_file)

####################################################################################################################################
if __name__ == '__main__':
    
    zk_hosts = None
    is_local = False
    image_file = None
    is_http = False
    no_depends_on = set()
    
    if sys.argv:
        arg_index = 0
        for arg in sys.argv:
            if arg == '-z' or arg == '--zookeeper':
                try:
                    zk_hosts = sys.argv[arg_index + 1]
                except:
                    pass
            if arg == '-i' or arg == '--image':
                try:
                    image_file = sys.argv[arg_index + 1]
                except:
                    pass
            elif arg == '-l' or arg == '--local':
                is_local = True
            elif arg == '-h' or arg == '--http':
                is_http = True
            arg_index += 1
    
    if not zk_hosts:
        try:
            with open('/etc/mesos/zk', 'r') as f:
                content = f.readlines()[0]
                zk_hosts = content[5:-7]
        except:
            logger.warning("Could not guess Zookeeper host and port")
    
    
    
    # Return a tuple, the first element is the text output and the second
    # indicates whether the graph could be generated or not.
    def get_output(image_path=None):
        output = ''
        pods_details, output = get_pods_details(is_local, output, hide_zookeeper_info = is_http)
        
        if not pods_details:
            return output, False           
        else:        
            if is_local:
                output += "Using local hardcoded config (for dev only).\n\n"
                #output += json.dumps(pods_details, sort_keys=True, indent=2, separators=(',', ': '))
                #output += "\n\n"
                
            G = get_graph(pods_details)        
            
            # The drawing of the graph will not be accurate in case of circular dependencies, so lets just not draw it.
            if len(list(nx.simple_cycles(G))) > 0:
                output += "Cannot draw dependency graph: there is something wrong with your pods config, it seems that you have a circular dependency.\n"
                output += "Details:\n"
                for t in list(nx.simple_cycles(G)):
                    # The last node, which is the same as the first one, is not listed in the list
                    # returned by simple_cycles, but lets still show it since it makes it more readable. 
                    first = None
                    output += "  "
                    for node in t:
                        if first is None:
                            first = node
                        output += node,
                        output += " --> "
                    output += first + "\n"      
                    first = None
                    
                return output, False
            # No circular dependency, lets proceed...
            else:
                ok_nodes, ko_nodes = get_nodes_status(G, pods_details)
                no_depends_on_me = get_no_depends_on(pods_details)
                if len(no_depends_on_me) > 0:
                    output += bcolors.FAIL + 'The following pods do not expose their dependencies, hence the graph is not reliable: ' + bcolors.ENDC + "\n"
                    for no_dep in no_depends_on_me:
                        output += no_dep + "\n\n"
                
                def is_process_running(pod_id):
                    if pods_details.has_key(pod_id):
                        body = pods_details.get(pod_id)[1]
                        if body.has_key("process"):
                            return "running" == body.get("process")
                    return False
                
                def draw_children(parent, graph, level, output):
                    ancestors = nx.ancestors(graph, parent)
                    for neighbor in nx.all_neighbors(graph, parent):
                        # all_neighbors() includes both predecessors and successors,
                        # so we need to make sure we do not enter an infinite loop...
                        if not neighbor in ancestors:
                            if is_process_running(neighbor):
                                color = bcolors.OKGREEN
                            else:
                                color = bcolors.FAIL
                            output += ('{spacer}' + color + '+-{t}').format(spacer='    ' * level, t=neighbor) + bcolors.ENDC + "\n"
                            output = draw_children(neighbor, graph, level + 1, output)
                    return output
                    
                # Draw the graph.
                if len(nx.nodes(G)) > 0:
                    
                    if image_path:
                        draw_image(G, ok_nodes, ko_nodes, image_path)
                    else:                        
                        output = draw_children(ROOT_NODE, G, 0, output)
                        output +=  '\n'
                    
                   
                    output += "Pods with a running process are shown in " + bcolors.OKGREEN + "green" + bcolors.ENDC + ", those with a non-running process in " + bcolors.FAIL+ "red" + bcolors.ENDC + ".\n"
                    
                    return output, True
                    
                else:
                    output += 'No pod to show. Have you any pod deployed!?\n'
                    return output, False
                

    
    if is_http:
        
        import BaseHTTPServer
        import urlparse
        
        HOST_NAME = ''
        PORT_NUMBER = 9000
        
        class MyHandler(BaseHTTPServer.BaseHTTPRequestHandler):
            # See https://wiki.python.org/moin/EscapingHtml
            html_escape_table = {
                "&": "&amp;",
                '"': "&quot;",
                "'": "&apos;",
                ">": "&gt;",
                "<": "&lt;",
            }
            
            def escape_html(self, text):
                if not text:
                    return text
                result = "".join(self.html_escape_table.get(c,c) for c in text)
                result = result.replace(" ", '&nbsp;')
                result = result.replace(bcolors.OKGREEN, '<span class="okGreen">')
                result = result.replace(bcolors.FAIL, '<span class="fail">')
                result = result.replace(bcolors.ENDC, '</span>')    
                result = result.replace("\n", '<br/>')
               
                return result       
            
            def get_random_image_name(self):
                def id_generator(size=8, chars=string.ascii_uppercase + string.digits):
                    return ''.join(random.choice(chars) for _ in range(size))
                
                while True:
                    file_name = "ochograph_" + id_generator() + ".png"
                    if not os.path.exists(file_name):
                        return file_name                       
                
            
            def do_HEAD(self):
                if self.path == '/':
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                elif self.path.startswith("/image/") and self.path.endswith(".png"):
                    image_name = self.path[self.path.rfind("/"):]
                    if not os.path.exists(image_name):
                        self.send_error(404, "File not found")
                    else:
                        self.send_response(200)
                        self.send_header("Content-type", 'image/png')
                        self.end_headers()
                elif self.path.startswith('/text') or self.path.startswith('/image'):
                    up = urlparse.urlparse(self.path)
                    if up.path == '/image' or up.path == '/text':
                        self.send_response(200)
                        self.send_header("Content-type", "text/html")
                        self.end_headers() 
                    else:
                        self.send_error(404, "File not found") 
                elif self.path == '/style.css':
                    self.send_response(200)
                    self.send_header("Content-type", "text/css")
                    self.end_headers()
                elif self.path == '/javascript.js':
                    self.send_response(200)
                    self.send_header("Content-type", "text/javascript")
                    self.end_headers()
                else:
                    self.send_error(404, "File not found")
            def do_GET(self):
                if self.path == '/':
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    self.wfile.write("<html><head><title>Ochograph</title>")
                    self.wfile.write('<link rel="stylesheet" type="text/css" href="style.css">')
                    self.wfile.write('</head>')
                    self.wfile.write("<body>")
                    self.wfile.write("<span class=\"title\">Ochograph</span><br/><br/>")
                    self.wfile.write("Visualize the dependencies and state of your Ochopod clusters in real-time.<br/><br/>")
                    self.wfile.write('<a href="/image">Image mode</a><br/>')
                    self.wfile.write('<a href="/text">Text mode</a>')
                    self.wfile.write('<br/><br/><br/><span class="footer"><a href="https://github.com/pferrot/ochograph" target="_blank">Ochograph on GitHub</a></span><br/><br/>')
                    self.wfile.write("</body></html>")
                elif self.path.startswith("/image/") and self.path.endswith(".png"):
                    image_name = self.path[self.path.rfind("/")+1:]
                    if not os.path.exists(image_name):
                        self.send_error(404, "File not found")
                    else:
                        self.send_response(200)
                        self.send_header("Content-type", 'image/png')
                        self.end_headers()
                        f = open(image_name, 'rb')
                        self.wfile.write(f.read())
                        f.close()
                        # Delete the image once it has been served.
                        try:
                            os.remove(image_name)
                        except:
                            logger.error("Failed to remove file: %s" % image_name) 
                elif self.path.startswith('/text') or self.path.startswith('/image'):
                    up = urlparse.urlparse(self.path)
                    if up.path == '/image' or up.path == '/text':                    
                        qs = urlparse.parse_qs(up.query)
                        auto_reload = False
                        if qs and qs.has_key("autoReload"):
                            v = qs.get("autoReload")
                            if v and len(v) > 0 and v[0] == '1':
                                auto_reload = True
                        self.send_response(200)
                        self.send_header("Content-type", "text/html")
                        self.end_headers()
                        self.wfile.write("<html><head><title>Ochograph</title>")
                        self.wfile.write('<link rel="stylesheet" type="text/css" href="style.css">')
                        self.wfile.write('<script src="javascript.js"></script>')
                        self.wfile.write('<script type="text/javascript">')
                        self.wfile.write('initAutoReload(%s);' % ("true" if auto_reload else "false"))
                        self.wfile.write('</script>')                                            
                        self.wfile.write('</head>')
                        self.wfile.write("<body>")
                        self.wfile.write("<span class=\"title\">Ochograph</span><br/>")
                        self.wfile.write("<span class=\"small\">Auto reload: <a href=\"javascript: void(0)\" id=\"autoReloadId\" onClick=\"toggleAutoReload();\">%s</a></span><br/>" % ("on" if auto_reload else "off"))
                        
                        image_file = None
                         
                        if up.path == '/text':
                            self.wfile.write("<span class=\"small\"><a href=\"/image\">Go to image mode</a></span><br/><br/>")
                        elif up.path == '/image':
                            self.wfile.write("<span class=\"small\"><a href=\"/text\">Go to text mode</a></span><br/><br/>")
                            image_file = self.get_random_image_name()
                            
                        output, graph_generated = get_output(image_file)
                        self.wfile.write(self.escape_html(output))
                        if image_file and graph_generated:
                            self.wfile.write("<br/><br/><br/>")
                            self.wfile.write('<img src="/image/%s"/>' % image_file)   
                            self.wfile.write("<br/><br/>")  
                            
                        self.wfile.write('<br/><br/><br/><span class="footer"><a href="https://github.com/pferrot/ochograph" target="_blank">Ochograph on GitHub</a></span><br/><br/>')                   
                        self.wfile.write("</body></html>")
                    else:
                        self.send_error(404, "File not found")
                elif self.path == '/style.css':
                    self.send_response(200)
                    self.send_header("Content-type", "text/css")
                    self.end_headers()                    
                    f = open('style.css')
                    self.wfile.write(f.read())
                    f.close() 
                elif self.path == '/javascript.js':
                    self.send_response(200)
                    self.send_header("Content-type", "text/javascript")
                    self.end_headers()                    
                    f = open('javascript.js')
                    self.wfile.write(f.read())
                    f.close() 
                else:
                    self.send_error(404, "File not found")
                
        
        server_class = BaseHTTPServer.HTTPServer
        httpd = server_class((HOST_NAME, PORT_NUMBER), MyHandler)
        logger.info("Server Starts - %s:%s" % (HOST_NAME, PORT_NUMBER))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        httpd.server_close()
        logger.info("Server Stops - %s:%s" % (HOST_NAME, PORT_NUMBER))
    
    else:
        
        print "\nOchograph"
        print "=========\n"
            
        output, graph_generated =  get_output(image_file)
        
        print output
        
        if image_file and graph_generated:
            print "Image has been created here: %s" % image_file
         
        # I find this more readable to finish with a new line :-)    
        print ''   
