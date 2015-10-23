# Ochograph
Ochograph allows to generate dependency graphs of your <a href="https://github.com/autodesk-cloud/ochopod" target="_blank">Ochopod</a> clusters, which is a great way of documenting your environment (<a href="https://en.wikipedia.org/wiki/A_picture_is_worth_a_thousand_words" target="_blank">"a picture is worth a thousands words"</a>). 

## Pre-requesites
For this tool to work, every single pod that has dependencies (i.e. the 'depends_on' variable of the Reactive class is not empty) must list its dependencies as a list of "&lt;cluster_name&gt;:&lt;port&gt;" under the 'dependsOn' key, e.g.:

```
def sanity_check(self, pid):
	# Will result in something like "mysql:3306"
	depends_on = cfg['db_cluster_name'] + ":" + cfg['db_cluster_port']

	# Result would then be {'dependsOn': ["mysql:3306"]}
	return {'dependsOn': [depends_on]}
```

Also, Zookeeper and the pods must be accessible (i.e. no firewall).

## Usage
- Install the necessary Python libraries:
  - networkx
  - kazoo
- python ochograph.py

Ochograph will try to guess the Zookeeper host(s) by reading the /etc/mesos/zk file. You can force a given Zookeeper host with the -z paramater, e.g. python ochograph.py -z 127.0.0.1:2181

## Result
For now, the result is raw text only, but image formats like PNG will likely be supported in the near future.

You will see something like this (very simple cluster here):

```
dev.ls-reverse-proxy #4
+-dev.cr-frontend #8
  +-dev.cr-app #31
  +-dev.cr-app #32
```
