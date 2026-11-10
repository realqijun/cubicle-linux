"""Execute the real plugin module at its OpenCode callback boundary."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node is required for the OpenCode JavaScript bridge tests")
class BridgeTests(unittest.TestCase):
    def run_js(self, script):
        module = (Path(__file__).resolve().parents[1] / "opencode" / "bridge.mjs").as_uri()
        fixture = """
          const hooks = new Map();
          const domain = name => ({hook: async (hook, callback) => {
            hooks.set(name + '.' + hook, callback); return {dispose: async () => {}};
          }});
          const ctx = {app: {version: '2.2.5'}, session: {...domain('session'),context:async()=>[]},
            tool: {...domain('tool'), transform: async callback => callback({
              list: () => [{id:'read'}, {id:'subagent'}], remove: () => {}
            })}, shell: domain('shell'), permission: domain('permission'),
            aisdk: domain('aisdk'),plugin:{list:async()=>({data:[]})},
            mcp:{transform:async callback=>callback({list:()=>[],remove:()=>{}})},
            reference:{transform:async callback=>callback({list:()=>[],remove:()=>{}})}};
        """
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e",
                                 f"import {{install, createChannel}} from {json.dumps(module)};\n" + fixture + script],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_denied_tool_is_blocked_before_effect(self):
        result = self.run_js("""
          let effect=false;
          await install(ctx, async (session, route, payload) => {
            if (route === 'tool.before') throw new Error('denied');
            return {allowed:true, receipt:'supervisor-receipt'};
          });
          await hooks.get('session.prompt')({sessionID:'session',messageID:'message',prompt:{text:'fixture'}});
          try {
            await hooks.get('tool.execute.before')({sessionID:'session',tool:'read',id:'call',input:{path:'.env'}});
            effect=true;
          } catch {}
          console.log(JSON.stringify({effect}));
        """)
        self.assertFalse(result["effect"])

    def test_channel_serializes_requests_and_rejects_denials(self):
        result = self.run_js("""
          const envelopes=[];
          const channel=createChannel({endpoint:'http://127.0.0.1:12345/admit',token:'a'.repeat(32)},
            async (url, options) => {
              envelopes.push(JSON.parse(options.body));
              return new Response(JSON.stringify({allowed:envelopes.length === 1,reason:'denied',receipt:'r'}));
            });
          const results=await Promise.allSettled([channel(null,'hello',{}), channel('session','tool.before',{})]);
          console.log(JSON.stringify({sequence:envelopes.map(e=>e.sequence),status:results.map(r=>r.status)}));
        """)
        self.assertEqual(result["sequence"], [1, 2])
        self.assertEqual(result["status"], ["fulfilled", "rejected"])

    def test_actual_native_bodies_and_auxiliary_contexts_are_admitted(self):
        result = self.run_js("""
          const seen=[];
          await install(ctx, async (session,route,payload) => {
            seen.push({route,payload}); return {allowed:true,receipt:'receipt-'+seen.length};
          });
          await hooks.get('session.prompt')({sessionID:'s',messageID:'m',prompt:{text:'fixture'}});
          for (const hook of ['context','compaction','generate','title']) {
            await hooks.get('session.'+hook)({sessionID:'s',system:[],messages:[{role:'user',content:[{type:'text',text:hook}]}],options:{}});
          }
          const request=new Request('https://provider.example/v1/chat/completions',{
            method:'POST',headers:{authorization:'SECRET_CREDENTIAL'},body:'{"messages":["EXACT_BODY"]}'});
          await hooks.get('session.http.request')({sessionID:'s',kind:'primary',request,model:{providerID:'fixture',id:'model'}});
          const response=new Response('data: EXACT_RESPONSE\\n\\n');
          await hooks.get('session.http.response')({sessionID:'s',kind:'primary',request,response});
          console.log(JSON.stringify({seen,originalRequest:await request.text(),originalResponse:await response.text()}));
        """)
        routes = [item["route"] for item in result["seen"]]
        self.assertTrue(all(route in routes for route in ("context", "compaction", "generate", "title")))
        sent = next(item["payload"] for item in result["seen"] if item["route"] == "provider.http.request")
        self.assertEqual(sent["body"], '{"messages":["EXACT_BODY"]}')
        self.assertEqual(result["originalRequest"], '{"messages":["EXACT_BODY"]}')
        self.assertNotIn("SECRET_CREDENTIAL", json.dumps(result))
        self.assertEqual(result["originalResponse"], "data: EXACT_RESPONSE\n\n")

    def test_summary_lineage_uses_stored_record_type_and_receipt(self):
        result = self.run_js("""
          const seen=[]; let transcript=[];
          ctx.session.context=async () => transcript;
          await install(ctx, async (session,route,payload) => {
            const receipt='receipt-'+seen.length; seen.push({route,payload,receipt}); return {allowed:true,receipt};
          });
          await hooks.get('session.prompt')({sessionID:'s',messageID:'user-id',prompt:{text:'fixture'}});
          await hooks.get('session.compaction')({sessionID:'s',system:[],messages:[],options:{}});
          const request=new Request('https://provider.example/v1/chat',{method:'POST',body:'{}'});
          await hooks.get('session.http.request')({sessionID:'s',kind:'compaction',request});
          await hooks.get('session.http.response')({sessionID:'s',kind:'compaction',request,response:new Response('summary')});
          transcript=[{id:'checkpoint-id',type:'compaction',status:'completed'}];
          await hooks.get('session.context')({sessionID:'s',system:[],messages:[{id:'checkpoint-id',role:'user',content:[{type:'text',text:'summary'}]}],options:{}});
          console.log(JSON.stringify({seen}));
        """)
        response = next(item for item in result["seen"] if item["route"] == "provider.http.response")
        context = next(item for item in result["seen"] if item["route"] == "context")
        self.assertEqual(context["payload"]["message_sources"], [
            {"message_id": "checkpoint-id", "kind": "compaction", "receipt": response["receipt"]}])

    def test_handshake_reports_successfully_registered_hooks(self):
        result = self.run_js("""
          let hello;
          await install(ctx, async (session,route,payload) => {
            if(route==='hello') hello=payload;
            return {allowed:true,receipt:'receipt'};
          });
          console.log(JSON.stringify({hello,actual:[...hooks.keys()]}));
        """)
        self.assertEqual(set(result["hello"]["registered"]), set(result["actual"]))
        self.assertEqual(len(result["actual"]), 17)

    def test_extra_plugin_blocks_prompt_and_mcp_catalog_is_removed(self):
        result = self.run_js("""
          const removed=[];const routes=[];
          ctx.mcp.transform=async callback=>callback({list:()=>[['extra',{}]],remove:name=>removed.push(name)});
          ctx.plugin.list=async()=>({data:[{id:'unreviewed',source:{type:'local'}}]});
          let blocked=false;
          try {
            await install(ctx,async(session,route)=>{routes.push(route);return {allowed:true,receipt:'r'};});
            await hooks.get('session.prompt')({sessionID:'s',messageID:'m',prompt:{text:'fixture'}});
          } catch {blocked=true;}
          console.log(JSON.stringify({blocked,removed,routes}));
        """)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["removed"], ["extra"])
        self.assertNotIn("prompt", result["routes"])

    def test_shell_environment_excludes_provider_and_control_credentials(self):
        result = self.run_js("""
          await install(ctx,async()=>({allowed:true,receipt:'r'}),{credentialNames:['FIXTURE_API_KEY']});
          await hooks.get('session.prompt')({sessionID:'s',messageID:'m',prompt:{text:'fixture'}});
          const event={command:'fixture',cwd:'/project',shell:'/bin/sh',timeout:1000,
            env:{FIXTURE_API_KEY:'DUMMY_KEY',PLANNER_BROKER_TOKEN:'DUMMY_TOKEN',OPENCODE_PASSWORD:'DUMMY_SERVER',PATH:'/usr/bin'}};
          await hooks.get('shell.create.before')(event);
          console.log(JSON.stringify({env:event.env}));
        """)
        self.assertEqual(result["env"], {"PATH": "/usr/bin"})

    def test_unsupported_transport_is_audited_without_frame_bytes_and_stays_blocked(self):
        result = self.run_js("""
          const seen=[];let effect=false;
          await install(ctx,async(session,route,payload)=>{
            seen.push({route,payload});return {allowed:true,receipt:'r'};
          });
          await hooks.get('session.prompt')({sessionID:'s',messageID:'m',prompt:{text:'fixture'}});
          try {
            await hooks.get('session.experimental.ws.send')({sessionID:'s',frame:'DUMMY_SECRET_FRAME'});
            effect=true;
          } catch {}
          console.log(JSON.stringify({seen,effect}));
        """)
        self.assertFalse(result["effect"])
        refusal = next(item for item in result["seen"] if item["route"] == "bridge.denial")
        self.assertEqual(refusal["payload"], {"code": "unsupported_websocket"})
        self.assertNotIn("DUMMY_SECRET_FRAME", json.dumps(result))

    def test_oversized_payload_is_audited_without_losing_sequence(self):
        result = self.run_js("""
          const sent=[];
          const channel=createChannel({endpoint:'http://127.0.0.1:12345/admit',token:'a'.repeat(32),maxBytes:256},
            async(url,options)=>{
              const envelope=JSON.parse(options.body);sent.push(envelope);
              return new Response(JSON.stringify({allowed:envelope.route!=='bridge.denial'}));
            });
          const results=await Promise.allSettled([
            channel('s','context',{text:'DUMMY_SECRET'.repeat(100)}),channel('s','context',{})]);
          console.log(JSON.stringify({sent,status:results.map(result=>result.status)}));
        """)
        self.assertEqual(result["status"], ["rejected", "fulfilled"])
        self.assertEqual([item["sequence"] for item in result["sent"]], [1, 2])
        self.assertEqual(result["sent"][0]["route"], "bridge.denial")
        self.assertNotIn("DUMMY_SECRET", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
