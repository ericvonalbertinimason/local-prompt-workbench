import importlib.util, os, tempfile, unittest, json
from pathlib import Path

TEMP=tempfile.TemporaryDirectory()
os.environ['HARNESS_DATA']=TEMP.name
spec=importlib.util.spec_from_file_location('harness_server',Path(__file__).with_name('server.py'))
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)

class HarnessTests(unittest.TestCase):
    def setUp(self):
        server.init()
        with server.db() as c:c.execute('DELETE FROM runs')
    def test_idempotency_and_conflicting_retry(self):
        a=server.create_run({'prompt':'A & B','request_id':'same-request-123'},'test')
        b=server.create_run({'prompt':'A & B','request_id':'same-request-123'},'test')
        self.assertEqual(a['id'],b['id'])
        with self.assertRaises(ValueError):server.create_run({'prompt':'changed','request_id':a['id']},'test')
    def test_revision_session_and_new_conversation_isolation(self):
        a=server.create_run({'prompt':'first'},'test')
        b=server.create_run({'prompt':'revise','parent_id':a['id']},'test')
        c=server.create_run({'prompt':'separate'},'test')
        self.assertEqual(a['session_id'],b['session_id'])
        self.assertNotEqual(a['session_id'],c['session_id'])
    def test_invalid_mode_cannot_bypass_mcp(self):
        with self.assertRaises(ValueError):server.create_run({'prompt':'x','mode':'skip'},'test')
    def test_questions_and_answers_carry_context_without_memory(self):
        a=server.create_run({'prompt':'Make a prompt for my project'},'test')
        with server.db() as c:c.execute("UPDATE runs SET response_kind='questions',questions=? WHERE id=?",(json.dumps(['What is the project?']),a['id']))
        b=server.create_run({'prompt':'An article summarizer','parent_id':a['id']},'test')
        self.assertIn('Make a prompt for my project',b['context_prompt'])
        self.assertIn('What is the project?',b['context_prompt'])
        self.assertIn('An article summarizer',b['context_prompt'])
        self.assertEqual(a['session_id'],b['session_id'])
        retry=server.create_run({'prompt':b['prompt'],'parent_id':a['id'],'request_id':b['id']},'test')
        self.assertEqual(b['id'],retry['id'])
    def test_question_xml_extraction_and_legacy_compatibility(self):
        r=server.parse_xml('<prompt_assistant kind="questions"><optimizer status="success"/><questions><question id="1">Purpose &amp; audience?</question></questions><result>Please clarify.</result></prompt_assistant>')
        self.assertEqual(r['questions'],['Purpose & audience?'])
        self.assertEqual(r['response_kind'],'questions')
        legacy=server.parse_xml('<prompt_assistant><optimizer status="success"/><result>Old prompt</result></prompt_assistant>')
        self.assertEqual(legacy['questions'],[])
    def test_output_requires_mcp_success_and_valid_xml(self):
        for s in ['<prompt_assistant><result>x</result></prompt_assistant>','bad xml']:
            with self.assertRaises(Exception):server.parse_xml(s)
        r=server.parse_xml('<prompt_assistant><execution id="123"/><optimizer status="success" duration_ms="42"><draft>A &amp; B</draft></optimizer><result>X &lt; Y</result></prompt_assistant>')
        self.assertEqual(r['optimizer_draft'],'A & B');self.assertEqual(r['result'],'X < Y')
    def test_action_pagination_is_lossless(self):
        row={'id':'x','status':'success','mode':'iterate-prompt','execution_id':'1','error':'','xml':'<xml>'+('x&'*18000)+'</xml>'}
        pieces=[];offset=0
        while True:
            r=server.action_status(row,offset);pieces.append(r['xml']);offset=r['next_offset']
            if offset is None:break
        self.assertEqual(''.join(pieces),row['xml'])
        self.assertTrue(all(len(x)<=12000 for x in pieces))
    def test_restart_does_not_replay_running_jobs(self):
        r=server.create_run({'prompt':'test'},'test')
        with server.db() as c:c.execute("UPDATE runs SET status='running' WHERE id=?",(r['id'],))
        server.init()
        with server.db() as c:self.assertEqual(c.execute('SELECT status FROM runs WHERE id=?',(r['id'],)).fetchone()[0],'interrupted')

if __name__=='__main__':unittest.main()
