import os
import shutil
import sys
import time
import traceback
from threading import Thread
from os import makedirs
import dagon


class Task(Thread):

    def __init__(self, name, command="",working_dir=None):
        Thread.__init__(self)
        self.name = name
        self.nexts = []
        self.prevs = []
        self.reference_count = 0
        self.remove_scratch_dir = False
        self.running = False
        self.workflow = None
        self.set_status(dagon.Status.READY)
        self.working_dir=working_dir
        self.command=command
        self.info=None

    def set_info(self, info):
        self.info = info

    def get_ip(self):
        return self.info["ip"]

    def get_info(self):
        return self.info

    def get_user(self):
        return self.info["user"]

    def get_scratch_dir(self):
        while self.working_dir is None and self.status is not dagon.Status.FAILED:
            time.sleep(1)
        return self.working_dir

    def get_scratch_name(self):
        millis = int(round(time.time() * 1000))
        return str(millis) + "-" + self.name

    # asJson
    def as_json(self):
        json_task = {"name": self.name, "status": self.status.name,
                    "working_dir": self.working_dir, "nexts":[], "prevs":[]}
        for t in self.nexts:
            json_task['nexts'].append(t.name)
        for t in self.prevs:
            json_task['prevs'].append(t.name)
        return json_task

    # Set the workflow
    def set_workflow(self, workflow):
        self.workflow = workflow

    # Set the current status
    def set_status(self, status):
        self.status = status
        if self.workflow is not None:
            self.workflow.logger.debug("%s: %s", self.name, self.status)
            if self.workflow.regist_on_api:
                self.workflow.api.update_task_status(self.workflow.id, self.name, status.name)

    # Add the dependency to a task
    def add_dependency_to(self, task):
        task.nexts.append(self)
        self.prevs.append(task)

        if self.workflow.regist_on_api: #add in the server
            self.workflow.api.add_dependency(self.workflow.id, self.name, task.name)

    # Increment the reference count
    def increment_reference_count(self):
        self.reference_count = self.reference_count + 1

    #Call garbage collector (remove scratch directory, container, cloud instace, etc)
    #implemented by each task class
    def on_garbage(self):
        shutil.move(self.working_dir, self.working_dir + "-removed")

    # Decremet the reference count
    def decrement_reference_count(self):
        self.reference_count = self.reference_count - 1

        # Check if the scratch directory must be removed
        if self.reference_count == 0 and self.remove_scratch_dir is True:
            # Call garbage collector (remove scratch directory, container, cloud instace, etc)
            self.on_garbage()
            # Perform some logging
            self.workflow.logger.debug("Removed %s", self.working_dir)

    # Method overrided
    def pre_run(self):
        # For each workflow:// in the command string
        ### Extract the referenced task
        ### Add a reference in the referenced task

        # Index of the starting position
        pos = 0

        # Forever unless no anymore Workflow.SCHEMA are present
        while True:
            # Get the position of the next Workflow.SCHEMA
            pos1 = self.command.find(dagon.Workflow.SCHEMA, pos)

            # Check if there is no Workflow.SCHEMA
            if pos1 == -1:
                # Exit the forever cycle
                break

            # Find the first occurrent of a whitespace (or if no occurrence means the end of the string)
            pos2 = self.command.find(" ", pos1)

            # Check if this is the last referenced argument
            if pos2 == -1:
                pos2 = len(self.command)

            # Extract the parameter string
            arg = self.command[pos1:pos2]

            # Remove the Workflow.SCHEMA label
            arg = arg.replace(dagon.Workflow.SCHEMA, "")

            # Split each argument in elements by the slash
            elements = arg.split("/")

            # Extract the referenced task's workflow name
            workflow_name = elements[0]

            # The task name is the first element
            task_name = elements[1]

            # Set the default workflow name if needed
            if workflow_name is None or workflow_name == "":
                workflow_name = self.workflow.name

            # Extract the reference task object
            task = self.workflow.find_task_by_name(workflow_name, task_name)

            # Check if the refernced task is consistent
            if task is not None:
                # Add the dependency to the task
                self.add_dependency_to(task)

                # Add the reference from the task
                task.increment_reference_count()

            # Go to the next element
            pos = pos2

    # Pre process command
    def pre_process_command(self, command):
        stager=dagon.Stager()

        # Initialize the script
        header="#! /bin/bash\n"
        header=header+"# This is the DagOn launcher script\n\n"

        # Add and execute the howim script
        context_script=header+self.get_how_im_script()+"\n\n"

        result = self.on_execute(context_script, "context.sh") #execute context script

        if result['code']:
            raise Exception(result['message'])

        ### start the creation of the launcher.sh script

        # Create the header
        header = header+"# Change the current directory to the working directory\n"
        header = header+"cd " + self.working_dir + "\n\n"

        header = header + "# Start staging in\n\n"

        # Create the body
        body = command

        # Index of the starting position
        pos = 0

        # Forever unless no anymore Workflow.SCHEMA are present
        while True:
            # Get the position of the next Workflow.SCHEMA
            pos1 = command.find(dagon.Workflow.SCHEMA, pos)

            # Check if there is no Workflow.SCHEMA
            if pos1 == -1:
                # Exit the forever cycle
                break

            # Find the first occurrent of a whitespace (or if no occurrence means the end of the string)
            pos2 = command.find(" ", pos1)

            # Check if this is the last referenced argument
            if pos2 == -1:
                pos2 = len(command)

            # Extract the parameter string
            arg = command[pos1:pos2]

            # Remove the Workflow.SCHEMA label
            arg = arg.replace(dagon.Workflow.SCHEMA, "")

            # Split each argument in elements by the slash
            elements = arg.split("/")

            # Extract the referenced task's workflow name
            workflow_name = elements[0]

            # The task name is the first element
            task_name = elements[1]

            # Get the rest of the string as local path
            local_path = arg.replace(workflow_name + "/" + task_name, "")

            # Set the default workflow name if needed
            if workflow_name is None or workflow_name == "":
                workflow_name = self.workflow.name

            # Extract the reference task object
            task = self.workflow.find_task_by_name(workflow_name, task_name)

            # Check if the refernced task is consistent
            if task is not None:
                # Evaluate the destiation path
                dst_path = self.working_dir+"/.dagon/inputs/" + workflow_name + "/" + task_name

                # Create the destination directory
                header = header + "# Create the destination directory\n"
                header = header + "mkdir -p " + dst_path + "/" + os.path.dirname(local_path) + "\n\n"

                # Add the move data command
                header=header+stager.stage_in(self,task,dst_path,local_path)

                # Change the body of the command
                body = body.replace(dagon.Workflow.SCHEMA + arg, dst_path + "/" + local_path)

            pos = pos2

        # Invoke the command
        header = header + "# Invoke the command\n"
        header = header + body + " |tee " + self.working_dir + "/.dagon/stdout.txt\n\n"
        return header

    # Post process the command
    def post_process_command(self, command):
        footer=command+"\n\n"
        footer=footer+"# Perform post process\n"
        footer+= "echo $?"
        return footer

    # Method to be overrided
    def on_execute(self, script, script_name):

        # The launcher script name
        script_name = self.working_dir + "/.dagon/" + script_name

        # Create a temporary launcher script
        file = open(script_name, "w")
        file.write(script)
        file.flush()
        file.close()
        os.chmod(script_name, 0744)

    # create path using mkdirs
    def mkdir_working_dir(self, path):
        makedirs(path)

    def create_working_dir(self):
        if self.working_dir is None:
            # Set a scratch directory as working directory
            self.working_dir = self.workflow.get_scratch_dir_base() + "/" + self.get_scratch_name()

            # Create scratch directory
            self.mkdir_working_dir(self.working_dir+"/.dagon")

            # Set to remove the scratch directory
            self.remove_scratch_dir = True

        self.workflow.logger.debug("%s: Scratch directory: %s", self.name, self.working_dir)
        if self.workflow.regist_on_api:  # change scratch directory on server
            try:
                self.workflow.api.update_task(self.workflow.id, self.name, "working_dir", self.working_dir)
            except Exception, e:
                self.workflow.logger.error("%s: Error updating scratch directory on server %s", self.name, e)


    def remove_reference_workflow(self):
        # Remove the reference
        # For each workflow:// in the command

        # Index of the starting position
        pos = 0

        # Forever unless no anymore Workflow.SCHEMA are present
        while True:
            # Get the position of the next Workflow.SCHEMA
            pos1 = self.command.find(dagon.Workflow.SCHEMA, pos)

            # Check if there is no Workflow.SCHEMA
            if pos1 == -1:
                # Exit the forever cycle
                break

            # Find the first occurrent of a whitespace (or if no occurrence means the end of the string)
            pos2 = self.command.find(" ", pos1)

            # Check if this is the last referenced argument
            if pos2 == -1:
                pos2 = len(self.command)

            # Extract the parameter string
            arg = self.command[pos1:pos2]

            # Remove the Workflow.SCHEMA label
            arg = arg.replace(dagon.Workflow.SCHEMA, "")

            # Split each argument in elements by the slash
            elements = arg.split("/")

            # Extract the referenced task's workflow name
            workflow_name = elements[0]

            # The task name is the first element
            task_name = elements[1]

            # Set the default workflow name if needed
            if workflow_name is None or workflow_name == "":
                workflow_name = self.workflow.name

            # Extract the reference task object
            task = self.workflow.find_task_by_name(workflow_name, task_name)

            # Check if the refernced task is consistent
            if task is not None:
                # Remove the reference from the task
                task.decrement_reference_count()

            # Go to the next element
            pos = pos2



    # Method execute
    def execute(self):
        self.create_working_dir()

        # Apply some command pre processing
        launcher_script = self.pre_process_command(self.command)

        # Apply some command post processing
        launcher_script = self.post_process_command(launcher_script)

        # Execute only if not dry
        if self.workflow.dry is False:
            # Invoke the actual executor
            self.result =self.on_execute(launcher_script, "launcher.sh")

            # Check if the execution failed
            if self.result['code']:
                raise Exception('Executable raised a execption ' + self.result['message'])

        self.remove_reference_workflow()

    def run(self):
        if self.workflow is not None:
            # Change the status
            self.set_status(dagon.Status.WAITING)

            # Wait for each previous tasks
            for task in self.prevs:
                task.join()

            # Check if one of the previous tasks crashed
            for task in self.prevs:
                if task.status == dagon.Status.FAILED:
                    self.set_status(dagon.Status.FAILED)
                    return

            # Change the status
            self.set_status(dagon.Status.RUNNING)

            # Execute the task Job
            try:
                self.workflow.logger.debug("%s: Executing...", self.name)
                self.execute()
            except Exception, e:
                print e.message.encode("utf-8")
                self.workflow.logger.error("%s: Except: %s", self.name, str(e))
                self.set_status(dagon.Status.FAILED)
                return
            #self.execute()

            # Start all next task
            for task in self.nexts:
                if task.status == dagon.Status.READY:
                    self.workflow.logger.debug("%s: Starting task: %s", self.name, task.name)
                    try:
                        task.start()
                    except:
                        self.workflow.logger.warn("%s: Task %s already started.", self.name, task.name)

            # Change the status
            self.set_status(dagon.Status.FINISHED)
            return

    def get_how_im_script(self):
        return """
        
# Initialize
machine_type="none"
public_id="none"
user="none"
status_sshd="none"
status_ftpd="none"

#get http communication protocol
curl_or_wget=$(if hash curl 2>/dev/null; then echo "curl"; elif hash wget 2>/dev/null; then echo "wget"; fi);


if [ $curl_or_wget = "wget" ]; then 
  public_ip=`wget -q -O- https://ipinfo.io/ip` 
else
  public_ip=`curl -s https://ipinfo.io/ip`
fi

if [ "$public_ip" == "" ]
then
  # The machine is a cluster frontend (or a single machine)
  machine_type="cluster-frontend"
  public_ip=`ifconfig 2>/dev/null| grep "inet "| grep -v "127.0.0.1"| awk '{print $2}'|grep -v "192.168."|grep -v "172.16."|grep -v "10."|head -n 1`
fi

if [ "$public_ip" == "" ]
then
  # If no public ip is available, then it is a cluster node
  machine_type="cluster-node"
  public_ip=`ifconfig 2>/dev/null| grep "inet "| grep -v "127.0.0.1"| awk '{print $2}'|head -n 1`  
fi


# Check if the secure copy is available
status_sshd=`service sshd status 2>/dev/null|grep "Active"| awk '{print $2}'`
if [ "$status_sshd" == "" ]
then
  status_sshd="none"
fi

# Check if the ftp is available
status_ftpd=`service vsftpd status 2>/dev/null|grep "Active"| awk '{print $2}'`
if [ "$status_ftpd" == "" ]
then
  status_ftpd="none"
fi

# Check if the grid ftp is available
status_gsiftpd=`service gsiftpd status 2>/dev/null|grep "Active"| awk '{print $2}'`
if [ "$status_gsiftpd" == "" ]
then
  status_gsiftpd="none"
fi

# Get the user
user=$USER

# Construct the json
json="{\\\"type\\\":\\\"$machine_type\\\",\\\"ip\\\":\\\"$public_ip\\\",\\\"user\\\":\\\"$user\\\",\\\"SCP\\\":\\\"$status_sshd\\\",\\\"FTP\\\":\\\"$status_ftpd\\\",\\\"GRIDFTP\\\":\\\"$status_gsiftpd\\\"}"

# Set the task info
if [ $curl_or_wget = "wget" ]; then
   wget -q  -O- --post-data=$json --header=Content-Type:application/json "http://"""+self.workflow.get_url()+"""/api/"""+self.name+"""/info"
else
   curl -s --header "Content-Type: application/json" --request POST --data \"$json\" http://"""+self.workflow.get_url()+"""/api/"""+self.name+"""/info
fi
  """
