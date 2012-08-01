<%namespace name="tw" module="tw2.core.mako_util"/>\
<html>
<head><title>${w.title or ''}</title></head>
<body ${tw.attrs(attrs=w.attrs)}>\
% if w.navbar:
${w.navbar.display() | n}\
%endif
% if w.user:
[Logged in as: ${w.user}]\
%endif
<h1>${w.title or ''}</h1>\
% if w.child:
${w.child.display() | n}\
%endif
% if w.newlink:
${w.newlink.display() | n}\
%endif
</body>
</html>