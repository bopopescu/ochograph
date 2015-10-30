FROM autodeskls/ochopod:1.0.6_20151029154900CET

RUN apt-get -y update && apt-get -y upgrade

RUN apt-get -y install python-matplotlib
RUN apt-get -y install graphviz
RUN apt-get -y install libgraphviz-dev
RUN apt-get -y install python-dev
RUN apt-get -y install wget
RUN apt-get -y install unzip

RUN pip install matplotlib
RUN pip install kazoo
RUN pip install networkx
RUN pip install pydot
# Say yes when prompted to uninstall.
RUN /usr/bin/yes | pip uninstall pyparsing
RUN pip install pyparsing==1.5.7

# Because pip install pygraphviz fails
RUN mkdir /pygraphviz-1.3.1_src
RUN wget https://pypi.python.org/packages/source/p/pygraphviz/pygraphviz-1.3.1.zip -P /pygraphviz-1.3.1_src
RUN unzip /pygraphviz-1.3.1_src/pygraphviz-1.3.1.zip -d /pygraphviz-1.3.1_src
RUN rm /pygraphviz-1.3.1_src/pygraphviz-1.3.1.zip
WORKDIR /pygraphviz-1.3.1_src/pygraphviz-1.3.1
RUN python setup.py install --include-path=/usr/include/graphviz --library-path=/usr/lib/graphviz
WORKDIR /
RUN rm -rf /pygraphviz-1.3.1_src

#
# - add our spiffy pod script + the ochograph code itself
# - add our supervisor script
# - start supervisor
#
ADD resources/pod /opt/ochograph/pod
ADD resources/ochograph.py /opt/ochograph/
ADD resources/style.css /opt/ochograph/
ADD resources/javascript.js /opt/ochograph/
ADD resources/supervisor /etc/supervisor/conf.d
CMD /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
