export function createPluginApi(api, pluginId) {
  const base = `plugin/${pluginId}`;

  function unwrap(response) {
    const body =
      response?.data?.success !== undefined ? response.data : response;
    if (!body) {
      throw new Error("MoviePilot 接口没有返回数据");
    }
    if (body.success === false) {
      throw new Error(body?.message || "MoviePilot 接口请求失败");
    }
    if (body.success === true) return body.data || {};
    return body.data ?? body;
  }

  return {
    async get(path) {
      return unwrap(await api.get(`${base}${path}`));
    },
    async post(path, payload = {}) {
      return unwrap(await api.post(`${base}${path}`, payload));
    },
  };
}

export function messageOf(error) {
  return error?.response?.data?.message || error?.message || "操作失败";
}
