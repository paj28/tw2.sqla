import tw2.core as twc, tw2.forms as twf, webob, sqlalchemy as sa, sys
import sqlalchemy.types as sat, tw2.dynforms as twd
from zope.sqlalchemy import ZopeTransactionExtension
import transaction, utils


try:
    from itertools import product
except ImportError:
    # Python 2.5 support
    def product(x, y):
        for i in x:
            for j in y:
                yield (i,j)

def is_relation(prop):
    return isinstance(prop, sa.orm.RelationshipProperty)

def is_onetoone(prop):
    if not is_relation(prop):
        return False

    if prop.direction == sa.orm.interfaces.ONETOMANY:
        if not prop.uselist:
            return True
    
    if prop.direction == sa.orm.interfaces.MANYTOONE:
        lis = list(prop._reverse_property)
        assert len(lis) == 1
        if not lis[0].uselist:
            return True

    return False

def is_manytomany(prop):
    return is_relation(prop) and \
            prop.direction == sa.orm.interfaces.MANYTOMANY

def is_manytoone(prop):
    if not is_relation(prop):
        return False

    if not prop.direction == sa.orm.interfaces.MANYTOONE:
        return False

    if is_onetoone(prop):
        return False

    return True

def is_onetomany(prop):
    if not is_relation(prop):
        return False

    if not prop.direction == sa.orm.interfaces.ONETOMANY:
        return False

    if is_onetoone(prop):
        return False

    return True

def sort_properties(prop1, prop2):
    """For now, we only want to make sure the many to many relation fields are
    displayed at the end of the HTML form
    """
    weight1 = 0
    weight2 = 0
    if is_manytomany(prop1):
        weight1 += 1
    if is_manytomany(prop2):
        weight2 += 1
    
    res = cmp(weight1, weight2)
    if res != 0:
        return res
    
    return cmp(prop1._creation_order, prop2._creation_order)


def required_widget(prop):
    """Returns bool

    Returns True if the widget corresponding to the given prop should be required
    """
    is_nullable = lambda prop: sum([c.nullable for c in getattr(prop, 'columns', [])])

    if not is_relation(prop):
        if not is_nullable(prop):
            return True
        return False

    if not is_manytoone(prop):
        return False

    localname = prop.local_side[0].name
    # If the local field is required, the relation should be required
    pkey = dict([(p.key, is_nullable(p)) for p in prop.parent.iterate_properties])
    return not pkey.get(localname, True)

class RelatedValidator(twc.IntValidator):
    """Validator for related object

    `entity`
        The SQLAlchemy class to use. This must be mapped to a single table with a single primary key column.
        It must also have the SQLAlchemy `query` property; this will be the case for Elixir classes,
        and can be specified using DeclarativeBase (and is in the TG2 default setup).
    """
    msgs = {
        'norel': 'No related object found',
    }

    def __init__(self, entity, required=False, **kw):
        super(RelatedValidator, self).__init__(**kw)
        cols = sa.orm.class_mapper(entity).primary_key
        if len(cols) != 1:
            raise twc.WidgetError('RelatedValidator can only act on tables that have a single primary key column')
        self.entity = entity
        self.primary_key = cols[0]
        self.required=required

    def to_python(self, value, state=None):
        if not value:
            if self.required:
                raise twc.ValidationError('required', self)
            return None

        # How could this happen (that we are already to_python'd)?
        if isinstance(value, self.entity):
            return value

        if isinstance(self.primary_key.type, sa.types.Integer):
            try:
                value = int(value)
            except ValueError:
                raise twc.ValidationError('norel', self)
        value = self.entity.query.filter(getattr(self.entity, self.primary_key.name)==value).first()
        if not value:
            raise twc.ValidationError('norel', self)
        return value

    def from_python(self, value, state=None):
        if not value:
            return value
        if not isinstance(value, self.entity):
            raise twc.ValidationError(
                'from_python not passed instance of self.entity but ' +
                'instead "%s" of type "%s".' % (str(value), str(type(value))))
        return value and unicode(sa.orm.object_mapper(value).primary_key_from_instance(value)[0])


class RelatedItemValidator(twc.Validator):

    def __init__(self, entity, required=False, **kw):
        super(RelatedItemValidator, self).__init__(**kw)
        self.required=required
        self.entity = entity
        self.item_validator = RelatedValidator(entity=self.entity)

    def to_python(self, value, state=None):
        value = [twc.safe_validate(self.item_validator, v) for v in value]
        value = [v for v in value if v is not twc.Invalid]
        if not value and self.required:
            raise twc.ValidationError('required', self)
        return value

    def from_python(self, value, state=None):
        return value


class DbPage(twc.Page):
    entity = twc.Param('SQLAlchemy mapped class to use', request_local=False)
    _no_autoid = True
    @classmethod
    def post_define(cls):
        if hasattr(cls, 'entity') and not hasattr(cls, 'title'):
            cls.title = twc.util.name2label(cls.entity.__name__)

class DbFormPage(DbPage, twf.FormPage):
    """
    A page that contains a form with database synchronisation. The `fetch_data` method loads a record
    from the database, based on the primary key in the URL (no parameters for a new record). The
    `validated_request` method saves the data to the database.
    """
    redirect = twc.Param('Location to redirect to after successful POST', request_local=False)
    _no_autoid = True

    def fetch_data(self, req):
        filter = dict([
            (key.__str__(), value) for key, value in req.GET.mixed().items()
        ])
        self.value = req.GET and self.entity.query.filter_by(**filter).first() or None

    @classmethod
    def validated_request(cls, req, data, protect_prm_tamp=True, do_commit=True):
        utils.update_or_create(cls.entity, data,
                               protect_prm_tamp=protect_prm_tamp)
        if do_commit:
            transaction.commit()

        if hasattr(cls, 'redirect'):
            return webob.Response(request=req, status=302, location=cls.redirect)
        else:
            return super(DbFormPage, cls).validated_request(req, data)


class DbListForm(DbPage, twf.FormPage):
    """
    A page that contains a list form with database synchronisation. The `fetch_data` method loads a full
    table from the database. The `validated_request` method saves the data to the database.
    """
    redirect = twc.Param('Location to redirect to after successful POST', request_local=False)
    _no_autoid = True

    def fetch_data(self, req):
        self.value = self.entity.query.all()
        
    @classmethod
    def validated_request(cls, req, data, protect_prm_tamp=True, do_commit=True):
        utils.from_list(cls.entity, cls.entity.query.all(), data,
                        force_delete=True, protect_prm_tamp=protect_prm_tamp)
        if do_commit:
            transaction.commit()

        if hasattr(cls, 'redirect'):
            return webob.Response(request=req, status=302, location=cls.redirect)
        else:
            return super(DbListForm, cls).validated_request(req, data)


class DbListPage(DbPage, twc.Page):
    """
    A page that contains a list with database synchronisation. The `fetch_data` method loads a full
    table from the database; there is no submit or write capability.    
    """
    newlink = twc.Param('New item widget', default=None)
    template = 'tw2.sqla.templates.dblistpage'
    _no_autoid = True

    def fetch_data(self, req):
        self.value = self.entity.query.all()

    @classmethod
    def post_define(cls):
        if cls.newlink:
            cls.newlink = cls.newlink(parent=cls)

    def __init__(self, **kw):
        super(DbListPage, self).__init__(**kw)
        if self.newlink:
            self.newlink = self.newlink.req()

    def prepare(self):
        super(DbListPage, self).prepare()
        if self.newlink:
            self.newlink.prepare()


class DbSelectionField(twf.SelectionField):
    entity = twc.Param('SQLAlchemy mapped class to use', request_local=False)

    def prepare(self):
        # avoid hardcoded id
        self.options = [(x.id, unicode(x)) for x in self.entity.query.all()]
        super(DbSelectionField, self).prepare()


class DbSingleSelectField(DbSelectionField, twf.SingleSelectField):
    @classmethod
    def post_define(cls):
        if getattr(cls, 'entity', None):
            required=getattr(cls, 'required', False)
            cls.validator = RelatedValidator(entity=cls.entity, required=required)

class DbCheckBoxList(DbSelectionField, twf.CheckBoxList):
    @classmethod
    def post_define(cls):
        if getattr(cls, 'entity', None):
            required=getattr(cls, 'required', False)
            cls.validator = RelatedItemValidator(required=required, entity=cls.entity)
            # We should keep item_validator to make sure the values are well transformed.
            cls.item_validator = RelatedValidator(entity=cls.entity)

class DbRadioButtonList(DbSelectionField, twf.RadioButtonList):
    @classmethod
    def post_define(cls):
        if getattr(cls, 'entity', None):
            required=getattr(cls, 'required', False)
            cls.validator = RelatedValidator(entity=cls.entity, required=required)

class DbCheckBoxTable(DbSelectionField, twf.CheckBoxTable):
    @classmethod
    def post_define(cls):
        if getattr(cls, 'entity', None):
            required=getattr(cls, 'required', False)
            cls.validator = RelatedItemValidator(required=required, entity=cls.entity)
            # We should keep item_validator to make sure the values are well transformed.
            cls.item_validator = RelatedValidator(entity=cls.entity)


class WidgetPolicy(object):
    """
    A policy object is used to generate widgets from SQLAlchemy properties.

    In general, the widget's id is set to the name of the property, and if the
    property is not nullable, the validator is set as required. If the desired
    widget is None, then no widget is used for that property.

    Several parameters can be overridden to select the widget to use:

    `pkey_widget`
        For primary key properties

    `onetomany_widget`
        For foreign key properties. In this case the widget's id is set to the
        name of the relation, and its entity is set to the target class.

    `manytoone_widget`
        For foreign key properties. In this case the widget's id is set to the
        name of the relation, and its entity is set to the target class.

    `name_widgets`
        A dictionary mapping property names to the desired widget. This can be
        used for names like "password" or "email".

    `type_widgets`
        A dictionary mapping SQLAlchemy property types to the desired widget.

    `default_widget`
        If the property does not match any of the other selectors, this is used.
        If this is None then an error is raised for properties that do not match.

    Alternatively, the `factory` method can be overriden to provide completely
    customised widget selection.
    """

    pkey_widget = None
    onetomany_widget = None
    manytoone_widget = None
    name_widgets = {}
    type_widgets = {}
    default_widget = None

    @classmethod
    def factory(cls, prop):
        widget = None
        if is_onetomany(prop):
            if not cls.onetomany_widget:
                raise twc.WidgetError(
                    "Cannot automatically create a widget " +
                    "for one-to-many relation '%s'" % prop.key)
            widget = cls.onetomany_widget(id=prop.key,entity=prop.mapper.class_, required=required_widget(prop))
        elif sum([c.primary_key for c in getattr(prop, 'columns', [])]):
            widget = cls.pkey_widget
        elif is_manytoone(prop):
            if not cls.manytoone_widget:
                raise twc.WidgetError(
                    "Cannot automatically create a widget " +
                    "for many-to-one relation '%s'" % prop.key)
            widget = cls.manytoone_widget(id=prop.key,entity=prop.mapper.class_, required=required_widget(prop))
        elif is_manytomany(prop):
            # Use the same widget as onetomany
            if not cls.onetomany_widget:
                raise twc.WidgetError(
                    "Cannot automatically create a widget " +
                    "for many-to-many relation '%s'" % prop.key)
            widget = cls.onetomany_widget(id=prop.key,entity=prop.mapper.class_, required=required_widget(prop))
        elif prop.key in cls.name_widgets:
            widget = cls.name_widgets[prop.key]
        else:
            for t, c in product(cls.type_widgets,
                                getattr(prop, 'columns', [])):
                if isinstance(c.type, t):
                    widget = cls.type_widgets[t]
                    break
            else:
                if not cls.default_widget:
                    raise twc.WidgetError(
                        "Cannot automatically create a widget " +
                        "for '%s'" % prop.key)
                widget = cls.default_widget

        if widget:
            args = {'id': prop.key}
            if required_widget(prop):
                args['validator'] = twc.Required
            widget = widget(**args)

        return widget


class NoWidget(twc.Widget):
    pass


class ViewPolicy(WidgetPolicy):
    """Base WidgetPolicy for viewing data."""
    manytoone_widget = twf.LabelField
    default_widget = twf.LabelField

    ## This gets assigned further down in the file.  It must, because of an
    ## otherwise circular dependency.
    #onetomany_widget = AutoViewGrid


class EditPolicy(WidgetPolicy):
    """Base WidgetPolicy for editing data."""
    # TODO -- actually set this to something sensible
    onetomany_widget = DbCheckBoxList
    manytoone_widget = DbSingleSelectField
    name_widgets = {
        'password':     twf.PasswordField,
        'email':        twf.TextField(validator=twc.EmailValidator),
        'ipaddress':    twf.TextField(validator=twc.IpAddressValidator),
    }
    type_widgets = {
        sat.String:     twf.TextField,
        sat.Integer:    twf.TextField(validator=twc.IntValidator),
        sat.DateTime:   twd.CalendarDateTimePicker,
        sat.Date:       twd.CalendarDatePicker,
        sat.Binary:     twf.FileField,
        sat.Boolean:    twf.CheckBox,
    }


class AutoContainer(twc.Widget):
    """
    An AutoContainer has its children automatically created from an SQLAlchemy entity,
    using a widget policy.
    """
    entity = twc.Param('SQLAlchemy mapped class to use', request_local=False)
    policy = twc.Param('WidgetPolicy to use')

    @classmethod
    def post_define(cls):
        if not hasattr(cls, 'entity') and hasattr(cls, 'parent') and hasattr(cls.parent, 'entity'):
            cls.entity = cls.parent.entity

        if hasattr(cls, 'entity') and not getattr(cls, '_auto_widgets', False):
            cls._auto_widgets = True
            fkey = dict((p.local_side[0].name, p)
                        for p in sa.orm.class_mapper(cls.entity).iterate_properties
                        if is_manytoone(p))
            new_children = []
            used_children = set()
            orig_children = getattr(cls.child, 'children', [])

            properties = sa.orm.class_mapper(cls.entity)._props.values()
            properties.sort(sort_properties)
            for prop in properties:
                if is_manytoone(prop):
                    continue

                # Swap ids and objs
                prop = fkey.get(prop.key, prop)

                widget_name = prop.key
                if isinstance(prop, sa.orm.RelationshipProperty):
                    widget_name = prop.local_side[0].name

                matches = [w for w in orig_children if w.key == widget_name]
                widget = len(matches) and matches[0] or None
                if widget:
                    if not issubclass(widget, NoWidget):
                        new_children.append(widget)
                    used_children.add(widget_name)
                else:
                    new_widget = cls.policy.factory(prop)
                    if new_widget:
                        new_children.append(new_widget)

            def child_filter(w):
                return w.key not in used_children and \
                       w.key not in [W.key for W in new_children]

            new_children.extend(filter(child_filter, orig_children))
            cls.child = cls.child(children=new_children, entity=cls.entity)


class AutoTableForm(AutoContainer, twf.TableForm):
    policy = EditPolicy

class AutoGrowingGrid(twd.GrowingGridLayout, AutoContainer):
    policy = EditPolicy

class AutoViewGrid(AutoContainer, twf.GridLayout):
    policy = ViewPolicy

# This is assigned here and not above because of a circular dep.
ViewPolicy.onetomany_widget = AutoViewGrid

class AutoListPage(DbListPage):
    _no_autoid = True
    class child(AutoViewGrid):
        pass

class AutoListPageEdit(AutoListPage):
    class edit(DbFormPage):
        _no_autoid = True
        child = AutoTableForm


# Borrowed from TG2
def commit_veto(environ, status, headers):
    """Veto a commit.

    This hook is called by repoze.tm in case we want to veto a commit
    for some reason. Return True to force a rollback.

    By default we veto if the response's status code is an error code.
    Override this method, or monkey patch the instancemethod, to fine
    tune this behaviour.

    """
    return not 200 <= int(status.split(None, 1)[0]) < 400

def transactional_session():
    """Return an SQLAlchemy scoped_session. If called from a script, use ZopeTransactionExtension so the session is integrated with repoze.tm. The extention is not enabled if called from the interactive interpreter."""
    return sa.orm.scoped_session(sa.orm.sessionmaker(autoflush=True, autocommit=False,
            extension=sys.argv[0] and ZopeTransactionExtension() or None))
