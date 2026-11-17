"""Meaningful transport/scoring checks with a fake HTTP endpoint; no model/GPU operations."""
import json, threading, time, unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import common

class ProtocolTests(unittest.TestCase):
    def fake(self, *, status='completed',reason=2,total=5,terminal=True):
        parent=self
        class Handler(BaseHTTPRequestHandler):
            calls=0
            def do_POST(self):
                Handler.calls+=1
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                def emit(event):
                    self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode());self.wfile.flush();time.sleep(.01)
                emit({'type':'response.reasoning_text.delta','delta':'reason'})
                emit({'type':'response.output_text.delta','delta':'ANSWER: C'})
                if terminal:
                    emit({'type':'response.'+status,'response':{'id':'fake-id','model':'fake-model','status':status,
                        'output':[{'type':'message','content':[{'type':'output_text','text':'ANSWER: C'}]}],
                        'usage':{'input_tokens':20,'output_tokens':total,'output_tokens_details':{'reasoning_tokens':reason}}}})
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:return common.collect('http://127.0.0.1:'+str(server.server_port),{'fake':True}),Handler.calls
        finally:server.shutdown();server.server_close();thread.join(timeout=2)
    def test_responses_counts_and_observed_boundaries(self):
        row,calls=self.fake();self.assertEqual(calls,1)
        self.assertEqual(row['reasoning_tokens'],2);self.assertEqual(row['non_reasoning_generated_tokens'],3)
        self.assertLess(row['first_any_output_s'],row['first_visible_content_s'])
        self.assertLess(row['first_visible_content_s'],row['terminal_answer_s'])
        self.assertTrue(row['stream_text_matches_terminal'])
    def test_truncated_visible_correct_answer_is_not_timed_success(self):
        row,_=self.fake(status='incomplete')
        task={'scorer':{'kind':'multiple_choice','expected':'C'}}
        score=common.score(task,row)
        self.assertTrue(score['answer_content_passed']);self.assertFalse(score['content_success'])
        self.assertFalse(any(score['correct_within_s'].values()))
    def test_missing_terminal_event_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError,'without a terminal'):self.fake(terminal=False)
    def test_invalid_provider_token_counts_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'inconsistent'):self.fake(reason=9,total=5)
    def test_format_and_content_errors_are_distinct(self):
        task={'scorer':{'kind':'json_exact','expected':{'x':3}}}
        row={'answer':'I calculated it.\n{"x":3}','provider_status':'completed','terminal_answer_s':2}
        grade=common.score(task,row);self.assertTrue(grade['format_only_failure']);self.assertTrue(grade['content_success'])
        row['answer']='{"x":4}';grade=common.score(task,row)
        self.assertTrue(grade['format_passed']);self.assertFalse(grade['answer_content_passed'])
    def test_completed_answer_deadline_not_first_token(self):
        row={'answer':'ANSWER: C','provider_status':'completed','terminal_answer_s':60.001,
             'first_any_output_s':1,'first_visible_content_s':2}
        score=common.score({'scorer':{'kind':'multiple_choice','expected':'C'}},row)
        self.assertEqual(score['correct_within_s'],{'30':False,'60':False,'120':True})
    def test_partial_output_is_not_recovered_as_a_complete_json_answer(self):
        row={'answer':'{"x":3','provider_status':'incomplete','terminal_answer_s':3}
        score=common.score({'scorer':{'kind':'json_exact','expected':{'x':3}}},row)
        self.assertFalse(score['answer_content_passed']);self.assertFalse(score['strict_success'])

if __name__=='__main__':unittest.main()
