def export(func=None, auth_level=None):
    def wrap(target):
        target._rpcserver_export = True
        target._rpcserver_auth_level = auth_level
        return target

    if func is None:
        return wrap
    return wrap(func)
