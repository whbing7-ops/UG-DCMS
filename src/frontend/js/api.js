/* API 访问层。
   令牌存在 sessionStorage 而非 localStorage: 关掉标签页即失效, 减少共用工作站上
   会话被他人接手的可能。系统同时有服务端会话撤销, 二者互补。 */
const BASE = "/api/v1";
const TOKEN_KEY = "dcms.token";

export function token()          { return sessionStorage.getItem(TOKEN_KEY); }
export function setToken(t)      { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); }

export class ApiError extends Error {
  constructor(status, body) {
    const e = (body && body.error) || {};
    const labels = {username:'账户名',password:'密码',old_password:'当前密码',new_password:'新密码',
      quantity:'数量',item_number:'项号',child_object_code:'子件号',name_cn:'名称',name_en:'英文名称',
      full_name:'姓名',email:'邮箱',file_number:'文件编号',file_type_code:'文件类型',
      external_part_number:'外部件号',namespace_code:'来源',manufacturer_code:'制造商',
      external_class_code:'外部件分类',software_number:'软件编号',software_type:'软件类型',
      project_code:'项目编号',project_applicability:'项目适用范围',project_evaluation_basis:'项目评价依据',
      approver_user_id:'审批人',reason:'原因',description:'说明',confirmation:'确认文字'};
    const msgCn = m => {
      if (!m) return '输入内容不符合要求';
      if (/at least 1 character/i.test(m)) return '不能为空';
      if (/field required|required/i.test(m)) return '为必填项';
      if (/valid integer/i.test(m)) return '必须是整数';
      if (/valid number|valid decimal/i.test(m)) return '必须是数字';
      if (/valid date/i.test(m)) return '日期格式不正确';
      if (/valid uuid/i.test(m)) return '标识格式不正确';
      return '输入内容不符合要求';
    };
    const validation = Array.isArray(body?.detail) ? body.detail.map(d => `${labels[d.loc?.at(-1)] || d.loc?.at(-1) || '输入内容'}：${msgCn(d.msg)}`).join('；') : null;
    const duplicate = {uq_app_user_username:'账户名已存在，请更换账户名',uq_app_user_email:'该邮箱已被其他账户使用'}[e.rule];
    super(duplicate || e.message || validation || `请求失败 (${status})`);
    this.status = status;
    this.code = e.code;
    this.rule = e.rule || null;   // 违反的不变量编号, 界面上要显示出来
    this.body = body;
  }
}

async function request(method, path, { json, form, query, raw } = {}) {
  let url = BASE + path;
  if (query) {
    const q = new URLSearchParams(
      Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== "")
    ).toString();
    if (q) url += (url.includes("?") ? "&" : "?") + q;
  }
  const headers = {};
  const t = token();
  if (t) headers.Authorization = "Bearer " + t;
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (form) body = form;

  const res = await fetch(url, { method, headers, body });

  if (res.status === 401 && path !== "/auth/login") {
    setToken(null);
    location.hash = "#/login";
    throw new ApiError(401, { error: { message: "会话已失效, 请重新登录" } });
  }
  if (raw) {
    if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null));
    return res;
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

export const api = {
  get:  (p, o) => request("GET", p, o),
  post: (p, o) => request("POST", p, o),
  patch:(p, o) => request("PATCH", p, o),
  put:  (p, o) => request("PUT", p, o),
  del:  (p, o) => request("DELETE", p, o),

  async download(path, filename) {
    const res = await request("GET", path, { raw: true });
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a"); a.href = url; a.download = filename;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  },
  login: (username, password) => request("POST", "/auth/login", { json: { username, password } }),
  me:    () => request("GET", "/auth/me"),
  logout:() => request("POST", "/auth/logout"),
  changePassword: (old_password, new_password) =>
    request("POST", "/auth/password", { json: { old_password, new_password } }),

  upload(path, fields, file) {
    const fd = new FormData();
    Object.entries(fields).forEach(([k, v]) => fd.append(k, v));
    fd.append("file", file);
    return request("POST", path, { form: fd });
  },
};
