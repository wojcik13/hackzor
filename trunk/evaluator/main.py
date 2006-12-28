import sys
import os
import time
import commands
import threading
import subprocess
import tempfile
import signal
import time
import pickle
import cookielib
import urllib2
import resource
import types
import base64
import StringIO
import xml.dom.minidom as minidom

from settings import *
import rules
import GPG

class EvaluatorError(Exception):
    def __init__(self, error, value=''):
        self.value = value
        self.error = error

    def __str__(self):
        return 'Error: '+self.error+'\tValue: '+ self.value


class XMLParser:
    """This base class will contain all the functions needed for the XML file
    to be parsed
    NOTE: This class is not to be instantiated
    """
    obj = GPG.GPG()
    def __init__(self):
        raise NotImplementedError('XMLParser class is not to be instantiated')

    def decrypt(self, value):
        if type(value) == types.ListType:
            ret_val = []
            for val in value:
                ret_val.append(self.obj.decrypt(val, always_trust=True))
            return ret_val
        return self.obj.decrypt(value, always_trust=True).data
    
    def get_val_by_id(self, root, id):
        """This function will get the value of the `id' child node of
        `root' node. `root' should be of type DOM Element. e.g.
        root
        |
        |-- <id>value-to-be-returned</id>"""
        child_node = root.getElementsByTagName(id)
        if not child_node:
            raise EvaluatorError('Invalid XML file')
        # dos2unix
        return self.decrypt(child_node[0].firstChild.nodeValue).replace('\r','')

    def add_node(self, doc, root, child, value):
        """ Used to add a text node 'child' with the value of 'value'(duh..) """
        node = doc.createElement(child)
        # print 'adding value', value
        value = self.obj.encrypt(value, SERVER_KEYID, always_trust=True).data
        node.appendChild(doc.createTextNode(value))
        root.appendChild(node)

    
class Question(XMLParser):
    """Defines the Characteristics of each question in the contest"""
    def __init__(self, qn, qid):
        #self.input_data = self.get_val_by_id(qn, 'input-data')
        input_path = self.get_val_by_id(qn, 'input-data')
        self.input_path = self.save_input_to_disk(input_path, qid)
        # TODO: Consider grouping all the contraint variables inside a `Limit'
        # class
        time_limit = self.get_val_by_id(qn, 'time-limit')
        self.time_limit = float(self.get_val_by_id(qn, 'time-limit'))
        self.mem_limit = 6400000 #int(self.get_val_by_id(qn, 'mem-limit'))
        self.eval_path = self.save_eval_to_disk(qn, qid)
        #print self.time_limit, self.mem_limit, self.eval_path

    def save_input_to_disk(self, input_data, qid):
        inp_path = os.path.join(INPUT_PATH, qid)
        inp_file = open(inp_path, 'w')
        input_data = str(input_data)
        inp_file.write(input_data)
        inp_file.close()
        return inp_path
        
    def save_eval_to_disk(self, qn, qid):
        """This function will unpickle the evaluator, sent from web server and
        save it into a file in the directory `evaluators' in the name of the
        question id"""
        # Save the pickled Evaluator binary to disk
        evaluator = self.decrypt(qn.getElementsByTagName('evaluator')[0].firstChild.nodeValue)
        eval_file_path = os.path.join(EVALUATOR_PATH, qid)
        ev = StringIO.StringIO()
        ev.write(evaluator)
        ev.seek(0)
#         eval_file = open(eval_file_path, 'w')
#         eval_file.write(evaluator)
#         eval_file.close()
        eval_file = open(eval_file_path, 'w')
        base64.decode(ev, eval_file)
        # del evaluator
#         evaluator = pickle.load(eval_file)
        eval_file.close()
#         eval_file = open(eval_file_path, 'w')
#         eval_file.write(evaluator.replace('\r','')) # dos2unix
#         eval_file.close()
        os.chmod(eval_file_path, 0700) # set executable permission for
                                       # evaluator
        return eval_file_path

    
class Questions(XMLParser):
    """Set of all questions in the contest"""
    def __init__(self, xml_file):
        xml = minidom.parseString(xml_file)
        qn_set = xml.getElementsByTagName('question-set')[0]
        if not qn_set:
            #return error here
            pass
        self.questions = {}
        for qn in qn_set.getElementsByTagName('question'):
            qid = str(qn.getAttribute('id').strip())
            self.questions[qid] = Question(qn, qid)
                        

class Attempt(XMLParser):
    """Each Attempt XML file is parsed by this class"""
    def __init__(self, xml_file):
        xml = minidom.parseString(xml_file)
        attempt = xml.getElementsByTagName('attempt')
        if not attempt:
            #return error here
            pass
        attempt = attempt[0]
        #print xml.toprettyxml()
        self.aid = self.get_val_by_id(attempt, 'aid')
        self.qid = self.get_val_by_id(attempt, 'qid')
        self.code = self.get_val_by_id(attempt, 'code')
        self.lang = self.get_val_by_id(attempt, 'lang')
        self.file_name = self.get_val_by_id(attempt, 'file-name')
        #print 'Booga: ', self.aid, self.qid, self.code, self.lang, self.file_name

    def convert_to_result(self, result, msg):
        """Converts an attempt into a corresponding XML file to notify result"""
        doc = minidom.Document()
        root = doc.createElementNS('http://code.google.com/p/hackzor', 'attempt')
        doc.appendChild(root)
        self.add_node(doc, root, 'aid', self.aid)
        self.add_node(doc, root, 'result', str(result))
        if int(result) in rules.rules.keys():
            msg = rules.rules[int(result)]
        self.add_node(doc, root, 'error', msg)
        return doc.toxml()
        
## TODO: Write about the parameter to methods in each of their doc strings
class Evaluator:
    """Provides the base functions for evaluating an attempt.
    NOTE: This class is not to be instantiated"""
    def __str__(self):
        raise NotImplementedError('Must be Overridden')

    def compile(self, code_file, input_file):
        raise NotImplementedError('Must be Overridden')

    def get_run_cmd(self, exec_file):
        raise NotImplementedError('Must be Overridden')

    def run(self, cmd, quest):
        input_file = quest.input_path
#         if cmd.startswith('java'):
#             input_file = os.path.join('..',input_file)
#         print input_file
        # Output_file = open('/tmp/output','w')#tempfile.NamedTemporaryFile()
        output_file = tempfile.NamedTemporaryFile()
        inp = open (input_file, 'r')
        kws = {'shell':True, 'stdin':inp, 'stdout':output_file.file}
        start_time = time.time()
        p = subprocess.Popen('./exec.py '+str(quest.mem_limit)+' '+cmd, **kws)
        while True:
            if time.time() - start_time >= quest.time_limit:
                # TODO: Verify this!!! IMPORTANT
                # os.kill(p.pid+1, signal.SIGTERM)
                # os.system('pkill -KILL -P '+str(p.pid)) # Try to implement pkill -P
                # internally
                # os.system('pkill '+cmd)
                status, psid = commands.getstatusoutput('pgrep -f '+cmd)
                # crude soln. Debatable whether to use or not
                psid = psid.splitlines()
                if os.getpid() in psid:
                    psid.remove(os.getpid()) # do not kill evaluator itself in
                                        # case of python TLE
                if psid == '':
                    print 'oO Problems of problems. Kill manually'
                    print cmd
                    psid = [p.pid]
                for proc in psid:
                    print 'Killing psid '+proc
                    try:
                        os.kill (int(proc), signal.SIGKILL)
                    except OSError:
                        # process does not exist
                        pass
                print 'Killed Process Tree: '+str(p.pid)
                raise EvaluatorError('Time Limit Expired')
            elif p.poll() != None:
                break
            time.sleep(0.5)
        print 'Return Value: ', p.returncode
        if p.returncode == 139:
            raise EvaluatorError('Run-Time Error. Received SIGSEGV')
        elif p.returncode == 137:
            raise EvaluatorError('Run-Time Error. Received SIGTERM')
        elif p.returncode == 143:
            raise EvaluatorError('Run-Time Error. Received SIGKILL')
        elif p.returncode != 0 :
            print p.returncode
            raise EvaluatorError('Run-Time Error. Unknown Error')
        else:
            output_file.file.flush()
            output_file.file.seek(0)
            # output_file.close()
            # output_file = open('/tmp/output','r')#tempfile.NamedTemporaryFile()
            output = output_file.file.read()
            output_file.close()
        return output

    def save_file(self, file_path, contents):
        """ Save the contents in the file given by file_path relative inside
        the WORK_DIR directory"""
        if not os.path.exists(WORK_DIR):
            os.mkdir(WORK_DIR)
        file_path = os.path.join(WORK_DIR, file_path)
        open_file = open(file_path, 'w')
        open_file.write(contents)
        open_file.close()
        return file_path

    def evaluate(self, attempt, quest):
        # Save the File
        save_loc = attempt.aid + '-' + attempt.qid + '-' + attempt.file_name
        code_file = self.save_file(save_loc, attempt.code)
        # Java has this dirty requirement that the file name be the same as the
        # main class name. So having a workaround. The attempts are saved
        #(for archival purposes only) and java files are also saved in a
        # temporary directory called java
        if attempt.lang.lower() == 'java':
            save_loc = os.path.join('java', attempt.file_name)
            code_file = self.save_file(save_loc, attempt.code)

        # Compile the File
        print os.getcwd()
        exec_file = self.compile(code_file)
        print os.getcwd()
        cmd = self.get_run_cmd(exec_file)
        print os.getcwd(), cmd        
        # Execute the file for preset input
        output = self.run(cmd, quest)
        # Match the output to expected output
        return self.check(attempt, output, quest.eval_path)

    def check(self, attempt, output, eval_path):
        op_file = tempfile.NamedTemporaryFile()
        op_file.file.write(output)
        op_file.file.flush()
        op_file.file.seek(0)
        kws = {'shell':True, 'stdin':op_file.file}
        p = subprocess.Popen(eval_path, **kws)
        p.wait()
        op_file.close()
        return str(p.returncode)


class C_Evaluator(Evaluator):
    def __init__(self):
        self.compile_cmd = C_COMPILE_STR
        
    def __str__(self):
        return 'C Evaluator'

    def get_run_cmd(self, exec_file):
        return exec_file

    def compile(self, code_file):
        output_file = code_file # Change this value to change output file name
        # replace the code with the object file
        cmd = self.compile_cmd.replace('%i',code_file).replace('%o',output_file)

        (status, output) = commands.getstatusoutput(cmd)
        if status != 0:
            raise EvaluatorError('Compiler Error', output)
        else:
            return output_file


class CPP_Evaluator(C_Evaluator):
    def __init__(self):
        self.compile_cmd = CPP_COMPILE_STR

    def __str__(self):
        return 'C++ Evaluator'
    

class Java_Evaluator(Evaluator):
    def __str__(self):
        return 'Java Evaluator'

    def __init__(self):
        self.compile_cmd = JAVA_COMPILE_STR

    def get_run_cmd(self, exec_file):
        return 'java '+exec_file

    def compile(self, code_file):
        output_dir, file_name = os.path.split(code_file)
        cmd = self.compile_cmd.replace('%i',code_file).replace('%o',
                                                                output_dir)
        if file_name [-5:] != '.java':
            raise EvaluatorError('Compiler Error', 'Not a Java File')
        file_name = file_name [:-5]
        (status, output) = commands.getstatusoutput(cmd)
        if status != 0:
            raise EvaluatorError('Compiler Error', output)
        else:
            return file_name


class Python_Evaluator(Evaluator):
    def __str__(self):
        return 'Python Evaluator'

    def compile(self, code_file):
        """ Nothing to Compile in the case of Python. Aha *Magic*!"""
        os.chmod(code_file,0700)
        return code_file

    def get_run_cmd(self, exec_file):
        #return 'python '+exec_file
        return exec_file
    

class Client:
    """ The Evaluator will evaluate and update the status """
    # TODO: Avoid HardCoding Language Options
    evaluators = {'c':C_Evaluator, 'c++':CPP_Evaluator,
                  'java':Java_Evaluator, 'python':Python_Evaluator}
    obj = GPG.GPG()
    def __init__(self):
        fpr = self.obj.fingerprints()[0]
        for key in self.obj.list_keys():
            if key['fingerprint'] == fpr:
                key_id = key['keyid']
        root_url = CONTEST_URL + '/opc/evaluator/'+key_id
        self.get_attempt_url = root_url + '/getattempt/'
        self.submit_attempt_url = root_url + '/submitattempt/'
        self.get_qns = root_url + '/getquestionset/'
        self.get_pub_key = root_url + '/getpubkey/'
        self.question_set = Questions(self.read_page(self.get_qns))
        # TODO: Get Pub Key automatically from the server
#         global SERVER_KEYID
#         server_key = self.read_page(self.get_pub_key)
#         SERVER_KEYID = self.import_key(server_key)

    def import_key(key):
        try:
            imp = self.obj.import_key(key)
        except KeyError:
            print 'Unable to import'
            return # TODO: Should it fail here?
        for keys in self.obj.list_keys():
            if keys['fingerprint'] == imp['fingerprint']:
                return keys['keyid']
        
    def read_page(self, website):
        cj = cookielib.CookieJar()
        cookie_opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cj))
        urllib2.install_opener(cookie_opener)
        req = urllib2.Request(website, None)
        data = urllib2.urlopen(req)
        data = data.read()
        return data
    
    def get_attempt(self):
        """ Keep polling the server until an attempt to be evaluated is
        obtained """
        req = urllib2.Request(self.get_attempt_url, None)
        req = urllib2.urlopen(req)
        return Attempt(req.read())

    def score(self, result, score):
        """Apply a function on the result to generate the score.. in case you
        want to have step wise scoring"""
        ## TODO: The scoring logic should be moved into the question setter's
        ## evaluator logic
        if result == True:
            return score
        else:
            return 0

    def evaluate(self, attempt, quest):
        """ Evaluate the attempt and return the ruling :-) 
            attempt : An instance of the Attempt Class
            return value : Boolean, the result of the evaluation.
        """
        lang = attempt.lang.lower()
        # first list special case languages whose names cannot be used for
        # function names in python
        try:
            evaluator = self.evaluators[lang]()
            return evaluator.evaluate(attempt, quest)
        except KeyError:
            raise NotImplementedError('Language '+lang+' not supported')
        except EvaluatorError:
            raise
            
    def submit_attempt(self, attempt_xml):
        host = CONTEST_URL
        #selector = self.submit_attempt_url_select
        selector = self.submit_attempt_url
        attempt_xml = self.obj.sign(attempt_xml).data
        headers = {'Content-Type': 'application/xml',
                   'Content-Length': str(len(attempt_xml))}
        r = urllib2.Request(self.submit_attempt_url, data=attempt_xml, headers=headers)
        return urllib2.urlopen(r).read()
        
    def start(self):
        print 'Evaluator Started'
        while True:
            #TODO: Temporary hack for catching 404, Corrrect it later
            try:
                print 'Waiting for Attempt'
                attempt = self.get_attempt()
            except urllib2.HTTPError:
                attempt = None
            if attempt == None:
                # No attempts in web server queue to evaluate
                time.sleep(TIME_INTERVAL)
                continue
            # evaluate the attempt
            try:
                return_value = self.evaluate(attempt, self.question_set.questions[str(attempt.qid)])
                msg = ''
            except EvaluatorError:
                print 'EvaluatorError: '
                msg = sys.exc_info()[1].error
                return_value = 3
            print 'Final Result: ', return_value, msg
            print self.submit_attempt(attempt.convert_to_result(return_value, msg))
        return return_value
if __name__ == '__main__':
    gpg = GPG.GPG()
    Client().start()
