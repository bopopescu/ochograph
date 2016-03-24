#
# Copyright (c) 2015 Autodesk Inc.
# All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import json
import os
import logging
import time

from ochopod.bindings.generic.marathon import Pod
from ochopod.models.piped import Actor as Piped

logger = logging.getLogger('ochopod')


if __name__ == '__main__':
    
    cfg = json.loads(os.environ['pod'])

    class Strategy(Piped):

        cwd = '/opt/ochograph'
        
        check_every = 60.0
        
        # So that we see the Ochograph logs in the Ochopod logs.
        pipe_subprocess = True

        pid = None

        since = 0.0

        def sanity_check(self, pid):

            #
            # - simply use the provided process ID to start counting time
            # - this is a cheap way to measure the sub-process up-time
            #
            now = time.time()
            if pid != self.pid:
                self.pid = pid
                self.since = now

            lapse = (now - self.since) / 3600.0

            return {'uptime': '%.2f hours (pid %s)' % (lapse, pid)}

        def configure(self, _):

            root_path = cfg['root_path'] if 'root_path' in cfg.keys() else None
            logger.debug("Using custom root path: %s" % root_path if root_path else "<no>")
            
            port_number = cfg['port_number'] if 'port_number' in cfg.keys() else None
            logger.debug("Using custom port number: %s" % port_number if port_number else "<no>")
            
            log_level = cfg['log_level'] if 'log_level' in cfg.keys() else "WARNING"
            
            return 'python ochograph.py -w %s %s %s' % (("-r %s" % root_path if root_path else ""), ("-p %s" % port_number if port_number else ""), ("--log %s" % log_level)), {}

    Pod().boot(Strategy)