// .NET Framework 4.x: no Python, database or administrator rights on LAN clients.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Diagnostics;
using System.IO;

sealed class ApiFailure:Exception { public int Status; public ApiFailure(int status,string text):base(text){Status=status;} }
sealed class NotificationItem { public long id; public string title; public string body; public string url; }
sealed class PairResult { public string token; public string full_name; }
sealed class PollResult { public NotificationItem[] items; public string full_name; }
sealed class NotifyClient:IDisposable {
    public string Origin;
    public string Token;
    readonly HttpClient http;
    readonly JavaScriptSerializer json=new JavaScriptSerializer();
    public NotifyClient(string origin){
        var u=new Uri(origin,UriKind.Absolute);
        if((u.Scheme!="http"&&u.Scheme!="https")||u.UserInfo!=""||u.AbsolutePath!="/"||u.Query!=""||u.Fragment!="")throw new Exception("服务器地址格式不正确");
        Origin=u.GetLeftPart(UriPartial.Authority);
        http=new HttpClient(new HttpClientHandler{AllowAutoRedirect=false});http.Timeout=TimeSpan.FromSeconds(12);
    }
    public async Task<T> Call<T>(string path,object body=null){
        using(var req=new HttpRequestMessage(body==null?HttpMethod.Get:HttpMethod.Post,Origin+"/api/v1/notifications/desktop/"+path)){
            if(Token!=null)req.Headers.Authorization=new AuthenticationHeaderValue("Bearer",Token);
            if(body!=null)req.Content=new StringContent(json.Serialize(body),System.Text.Encoding.UTF8,"application/json");
            using(var res=await http.SendAsync(req)){
                var text=await res.Content.ReadAsStringAsync();
                if(!res.IsSuccessStatusCode)throw new ApiFailure((int)res.StatusCode,(int)res.StatusCode==401?"绑定或登录已失效，请登录网页后重新生成绑定信息":"服务器暂时不可用（"+(int)res.StatusCode+"）");
                return json.Deserialize<T>(text);
            }
        }
    }
    public void Dispose(){http.Dispose();Token=null;}
}
sealed class NotifyForm:Form {
    readonly TextBox pairing=new TextBox{Dock=DockStyle.Top};
    readonly Label status=new Label{Dock=DockStyle.Fill,Text="请从 UG-DCMS 登录首页生成绑定信息，再粘贴到上方。",Padding=new Padding(10)};
    readonly Button connect=new Button{Text="连接通知",Dock=DockStyle.Top,Height=36};
    readonly NotifyIcon tray=new NotifyIcon{Icon=SystemIcons.Information,Text="UG-DCMS 通知助手",Visible=true};
    readonly Timer timer=new Timer{Interval=15000};
    readonly HashSet<long> shown=new HashSet<long>();
    NotifyClient client;
    string clickUrl;
    bool polling=false,closing=false;
    public NotifyForm(){
        Text="UG-DCMS Windows 通知助手 · rc2.41";Width=620;Height=260;MinimumSize=new Size(480,240);
        Font=new Font("Microsoft YaHei UI",10);StartPosition=FormStartPosition.CenterScreen;
        var help=new Label{Text="粘贴网页首页生成的绑定信息（5分钟内有效）：",Dock=DockStyle.Top,Height=32};
        Controls.Add(status);Controls.Add(connect);Controls.Add(pairing);Controls.Add(help);
        var menu=new ContextMenuStrip();menu.Items.Add("显示通知助手",null,(s,e)=>Restore());
        menu.Items.Add("打开待办事项",null,(s,e)=>Open("#/"));
        menu.Items.Add("发送测试通知",null,(s,e)=>tray.ShowBalloonTip(8000,"UG-DCMS 测试通知","Windows 通知助手已运行。",ToolTipIcon.Info));
        menu.Items.Add("断开并退出",null,async(s,e)=>await Quit());tray.ContextMenuStrip=menu;
        tray.DoubleClick+=(s,e)=>Restore();tray.BalloonTipClicked+=(s,e)=>Open(clickUrl??"#/");
        connect.Click+=async(s,e)=>await Connect();timer.Tick+=async(s,e)=>await Poll();
        FormClosing+=(s,e)=>{if(!closing&&e.CloseReason==CloseReason.UserClosing){e.Cancel=true;Hide();}};
        Resize+=(s,e)=>{if(WindowState==FormWindowState.Minimized)Hide();};
    }
    void Restore(){Show();WindowState=FormWindowState.Normal;Activate();}
    void Open(string relative){
        if(client==null)return;
        // Only application-local routes may be launched by a received message.
        if(!relative.StartsWith("#/approval/")&&relative!="#/")return;
        Process.Start(new ProcessStartInfo(client.Origin+"/"+relative){UseShellExecute=true});
    }
    async Task Connect(){
        if(polling){status.Text="正在同步，请稍后再连接。";return;}
        connect.Enabled=false;timer.Stop();
        try{
            var parts=pairing.Text.Trim().Split('|');if(parts.Length!=2)throw new Exception("请粘贴完整绑定信息，包含服务器地址和绑定码");
            if(client!=null)client.Dispose();client=new NotifyClient(parts[0]);shown.Clear();
            var r=await client.Call<PairResult>("redeem",new{code=parts[1]});client.Token=r.token;
            pairing.Clear();clickUrl="#/";status.Text="已连接："+r.full_name+"\n"+client.Origin+"\n保持助手运行即可接收提醒。关闭窗口会收起到系统托盘；退出登录或会话到期后需重新绑定。";
            tray.ShowBalloonTip(8000,"UG-DCMS 通知已连接","待审批申请和审批结果将在此提醒；点击打开待办。",ToolTipIcon.Info);
            timer.Start();await Poll();
        }catch(Exception ex){status.Text=ex.Message;}
        finally{connect.Enabled=true;}
    }
    async Task Poll(){
        if(polling||client==null||client.Token==null)return;polling=true;
        try{
            var r=await client.Call<PollResult>("poll");var items=r.items??new NotificationItem[0];
            var fresh=items.Where(x=>!shown.Contains(x.id)).ToArray();
            if(fresh.Length>0){
                clickUrl=fresh.Length==1?fresh[0].url:"#/";
                string title=fresh.Length==1?fresh[0].title:"您有 "+fresh.Length+" 条审批消息";
                string body=fresh.Length==1?fresh[0].body:string.Join("\n",fresh.Take(3).Select(x=>x.title+"："+x.body));
                if(body.Length>240)body=body.Substring(0,240)+"…";
                tray.ShowBalloonTip(10000,"UG-DCMS · "+title,body,ToolTipIcon.Info);
                foreach(var x in fresh)shown.Add(x.id);
            }
            if(items.Length>0)await client.Call<object>("ack",new{ids=items.Select(x=>x.id).ToArray()});
            status.Text="正在接收："+r.full_name+"\n"+client.Origin+"\n最近检查："+DateTime.Now.ToString("HH:mm:ss")+"；可收起到系统托盘。";
        }catch(ApiFailure ex){status.Text=ex.Message;if(ex.Status==401){timer.Stop();client.Token=null;tray.ShowBalloonTip(8000,"UG-DCMS 通知已停止",ex.Message,ToolTipIcon.Warning);}}
        catch(Exception){status.Text="暂时无法连接服务器，15秒后重试。消息会保留，恢复连接后继续提醒。";}
        finally{polling=false;}
    }
    async Task Quit(){timer.Stop();closing=true;tray.Visible=false;tray.Dispose();if(client!=null)client.Dispose();await Task.FromResult(0);Close();}
    [STAThread] public static int Main(string[] args){
        ServicePointManager.SecurityProtocol=SecurityProtocolType.Tls12;
        if(args.Length==3&&args[0]=="--http-test"){
            try{using(var c=new NotifyClient(args[1])){
                var p=c.Call<PairResult>("redeem",new{code=args[2]}).GetAwaiter().GetResult();c.Token=p.token;
                var r=c.Call<PollResult>("poll").GetAwaiter().GetResult();
                if(r.items.Length!=1||r.items[0].title!="申请审批通过")return 2;
                c.Call<object>("ack",new{ids=r.items.Select(x=>x.id).ToArray()}).GetAwaiter().GetResult();return 0;
            }}catch{return 3;}
        }
        if(args.Length>0&&args[0]=="--self-test")return SelfTest(args.Length>1?args[1]:"notify-self-test.txt");
        bool created;using(var mutex=new System.Threading.Mutex(true,"Local\\UGDCMS-Notify-"+Environment.UserName,out created)){
            if(!created){MessageBox.Show("通知助手已在运行，请从任务栏右下角打开。","UG-DCMS");return 0;}
            Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);Application.Run(new NotifyForm());return 0;
        }
    }
    static int SelfTest(string path){
        // Exercises real Windows UI and notification calls; no claim that the OS displayed a toast.
        try{
            Application.EnableVisualStyles();using(var form=new NotifyForm()){
                form.Show();Application.DoEvents();
                form.tray.ShowBalloonTip(1000,"UG-DCMS CI 通知测试","【模拟数据】待审批消息",ToolTipIcon.Info);Application.DoEvents();
                var sample=new JavaScriptSerializer().Deserialize<PollResult>("{\"items\":[{\"id\":1,\"title\":\"审批通过\",\"body\":\"测试\",\"url\":\"#/approval/test\"}],\"full_name\":\"测试账户\"}");
                if(sample.items[0].title!="审批通过")throw new Exception("JSON decoding failed");
                form.closing=true;form.tray.Visible=false;form.tray.Dispose();form.Close();
            }
            File.WriteAllText(path,"PASS: Windows form, tray, notification API and Unicode payload; actual popup visibility requires an interactive user session.");return 0;
        }catch(Exception e){File.WriteAllText(path,e.ToString());return 1;}
    }
}
