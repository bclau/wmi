# -*- coding: utf-8 -*-
ur"""Windows Management Instrumentation (WMI) is Microsoft's answer to
the DMTF's Common Information Model. It allows you to query just
about any conceivable piece of information from any computer which
is running the necessary agent and over which have you the
necessary authority.

The implementation is by means of COM/DCOM and most of the examples
assume you're running one of Microsoft's scripting technologies.
Fortunately, Mark Hammond's pywin32 has pretty much all you need
for a workable Python adaptation. I haven't tried any of the fancier
stuff like Async calls and so on, so I don't know if they'd work.

Since the COM implementation doesn't give much away to Python
programmers, I've wrapped it in some lightweight classes with
some getattr / setattr magic to ease the way. In particular:

* The _wmi_namespace object itself will determine its classes
  and allow you to return all instances of any of them by
  using its name as an attribute. As an additional shortcut,
  you needn't specify the Win32\_; if the first lookup fails
  it will try again with a Win32\_ on the front::

    disks = wmi.WMI ().Win32_LogicalDisk ()

  In addition, you can specify what would become the WHERE clause
  as keyword parameters::

    fixed_disks = wmi.WMI ().Win32_LogicalDisk (DriveType=3)
    
* The objects returned by a WMI lookup are wrapped in a Python
  class which determines their methods and classes and allows
  you to access them as though they were Python classes. The
  methods only allow named parameters::

    for p in wmi.WMI ().Win32_Process ():
      if p.Name.lower () == 'notepad.exe':
        p.Terminate (Result=1)

* Doing a print on one of the WMI objects will result in its
  GetObjectText\_ method being called, which usually produces
  a meaningful printout of current values.
  The repr of the object will include its full WMI path,
  which lets you get directly to it if you need to.

* You can get the associators and references of an object as
  a list of python objects by calling the associators () and
  references () methods on a WMI Python object::
    
    for p in wmi.WMI ().Win32_Process ():
      if p.Name.lower () == 'notepad.exe':
        for r in p.references ():
          print r.Name

* WMI classes (as opposed to instances) are first-class
  objects, so you can get hold of a class, and call
  its methods or set up a watch against it::

    process = wmi.WMI ().Win32_Process
    process.Create (CommandLine="notepad.exe")

* To make it easier to use in embedded systems and py2exe-style
  executable wrappers, the module will not force early Dispatch.
  To do this, it uses a handy hack by Thomas Heller for easy access
  to constants.

Typical usage will be::

  import wmi

  vodev1 = wmi.WMI ("vodev1")
  for disk in vodev1.Win32_LogicalDisk ():
    if disk.DriveType == 3:
      space = 100 * long (disk.FreeSpace) / long (disk.Size)
      print "%s has %d%% free" % (disk.Name, space)

Many thanks, obviously to Mark Hammond for creating the win32all
extensions, but also to Alex Martelli and Roger Upole, whose
c.l.py postings pointed me in the right direction.
Thanks especially in release 1.2 to Paul Tiemann for his code
contributions and robust testing.
© Tim Golden - mail at timgolden.me.uk 5th June 2003

Licensed under the (GPL-compatible) MIT License:

http://www.opensource.org/licenses/mit-license.php

For change history see CHANGELOG.TXT
"""
try:
  True, False
except NameError:
  True = 1
  False = 0

try:
  object
except NameError:
  class object: pass

__VERSION__ = "1.4"

_DEBUG = False

import sys
import re
import struct
import datetime
from win32com.client import GetObject, Dispatch
import pywintypes

class _ProvideConstants (object):
  ur"""A class which, when called on a win32com.client.Dispatch object,
  provides lazy access to constants defined in the typelib.

  They can be accessed as attributes of the _constants property.
  From Thomas Heller on c.l.py
  """
  def __init__(self, comobj):
    comobj.__dict__["_constants"] = self
    self.__typecomp = \
    comobj._oleobj_.GetTypeInfo().GetContainingTypeLib()[0].GetTypeComp()

  def __getattr__(self, name):
    if name.startswith("__") and name.endswith("__"):
      raise AttributeError, name
    result = self.__typecomp.Bind(name)
    #
    # Bind returns a 2-tuple, first item is TYPEKIND,
    # the second item has the value
    #
    if not result[0]:
      raise AttributeError, name
    return result[1].value

obj = GetObject ("winmgmts:")
_ProvideConstants (obj)

wbemErrInvalidQuery = obj._constants.wbemErrInvalidQuery
wbemErrTimedout = obj._constants.wbemErrTimedout
wbemFlagReturnImmediately = obj._constants.wbemFlagReturnImmediately
wbemFlagForwardOnly = obj._constants.wbemFlagForwardOnly

#
# Exceptions
#
class x_wmi (Exception):
  ur"Base for all WMI-related exceptions"

class x_wmi_invalid_query (x_wmi):
  ur"Raised when a WMI query is invalid"

class x_wmi_timed_out (x_wmi):
  ur"Raised when a watcher times out"

class x_wmi_no_namespace (x_wmi):
  ur"Raised when attempting to query a class with no namespace"

WMI_EXCEPTIONS = {
  wbemErrInvalidQuery : x_wmi_invalid_query,
  wbemErrTimedout : x_wmi_timed_out
}

def signed_to_unsigned (signed):
  ur"""Convert a (possibly signed) long to unsigned hex"""
  unsigned, = struct.unpack ("L", struct.pack ("l", signed))
  return unsigned

def handle_com_error (error_info):
  ur"""Convenience wrapper for displaying all manner of COM errors.
  Raises a x_wmi exception with more useful information attached
  
  :param error_info: The structure attached to a pywintypes.com_error
  :raises: :exc:`x_wmi` with a suitably formatted string
  """
  hresult_code, hresult_name, additional_info, parameter_in_error = error_info
  exception_string = [u"%08X - %s" % (signed_to_unsigned (hresult_code), hresult_name.decode ("mbcs"))]
  if additional_info:
    wcode, source_of_error, error_description, whlp_file, whlp_context, scode = additional_info
    exception_string.append (u"  Error in: %s" % source_of_error.decode ("mbcs"))
    exception_string.append (u"  %08X - %s" % (signed_to_unsigned (scode), (error_description or "").decode ("mbcs").strip ()))
  raise x_wmi, u"\n".join (exception_string)

BASE = datetime.datetime (1601, 1, 1)
def from_1601 (ns100):
  ur"""Convenience function to convert WMI timestamps -- which represent the
  number of 100ns intervals since 1601 -- to a normal datetime object.
  
  :param ns100: a WMI timestamp
  :returns: a datetime value
  """
  return BASE + datetime.timedelta (microseconds=int (ns100) / 10)

def from_time (year=None, month=None, day=None, hours=None, minutes=None, seconds=None, microseconds=None, timezone=None):
  ur"""Convenience wrapper to take a series of date/time elements and return a WMI time
  of the form yyyymmddHHMMSS.mmmmmm+UUU. All elements may be int, string or
  omitted altogether. If omitted, they will be replaced in the output string
  by a series of stars of the appropriate length.
  
  :param year: The year element of the date/time
  :param month: The month element of the date/time
  :param day: The day element of the date/time
  :param hours: The hours element of the date/time
  :param minutes: The minutes element of the date/time
  :param seconds: The seconds element of the date/time
  :param microseconds: The microseconds element of the date/time
  :param timezone: The timeezone element of the date/time
  
  :returns: A WMI datetime string of the form: yyyymmddHHMMSS.mmmmmm+UUU
  """
  def str_or_stars (i, length):
    if i is None:
      return u"*" * length
    else:
      return unicode (i).rjust (length, u"0")

  wmi_time = u""
  wmi_time += str_or_stars (year, 4)
  wmi_time += str_or_stars (month, 2)
  wmi_time += str_or_stars (day, 2)
  wmi_time += str_or_stars (hours, 2)
  wmi_time += str_or_stars (minutes, 2)
  wmi_time += str_or_stars (seconds, 2)
  wmi_time += "."
  wmi_time += str_or_stars (microseconds, 6)
  wmi_time += str_or_stars (timezone, 4)

  return wmi_time

def to_time (wmi_time):
  ur"""Convenience wrapper to take a WMI datetime string of the form 
  yyyymmddHHMMSS.mmmmmm+UUU and return a 9-tuple containing the
  individual elements, or None where string contains placeholder
  stars.
  
  :param wmi_time: The WMI datetime string in yyyymmddHHMMSS.mmmmmm+UUU format
  
  :returns: A 9-tuple of (year, month, day, hours, minutes, seconds, microseconds, timezone)
  """
  def int_or_none (s, start, end):
    try:
      return int (s[start:end])
    except ValueError:
      return None

  year = int_or_none (wmi_time, 0, 4)
  month = int_or_none (wmi_time, 4, 6)
  day = int_or_none (wmi_time, 6, 8)
  hours = int_or_none (wmi_time, 8, 10)
  minutes = int_or_none (wmi_time, 10, 12)
  seconds = int_or_none (wmi_time, 12, 14)
  microseconds = int_or_none (wmi_time, 15, 21)
  timezone = wmi_time[21:]

  return year, month, day, hours, minutes, seconds, microseconds, timezone

def _set (obj, attribute, value):
  ur"""Helper function to add an attribute directly into the instance
  dictionary, bypassing possible __getattr__ calls
  
  :param obj: Any python object
  :param attribute: String containing attribute name
  :param value: Any python object
  """
  obj.__dict__[attribute] = value

class _wmi_method (object):
  ur"""A currying sort of wrapper around a WMI method name. It
  abstract's the method's parameters and can be called like
  a normal Python object passing in the parameter values.
  
  Output parameters are returned from the call as a tuple.
  In addition, the docstring is set up as the method's
  signature, including an indication as to whether any
  given parameter is expecting an array, and what
  special privileges are required to call the method.
  """

  def __init__ (self, ole_object, method_name):
    """
    :param ole_object: The WMI class/instance whose method is to be called
    :param method_name: The name of the method to be called
    """
    
    def parameter_names (method_parameters):
      parameter_names = []
      for param in method_parameters.Properties_:
        name, is_array = param.Name, param.IsArray
        datatype = bitmap = None
        for qualifier in param.Qualifiers_:
          if qualifier.Name == "CIMTYPE":
            datatype = qualifier.Value
          elif qualifier.Name == "BitMap":
            bitmap = [int (b) for b in qualifier.Value]
        parameter_names.append ((name, is_array, datatype, bitmap))
      return parameter_names
      
    try:
      self.ole_object = Dispatch (ole_object)
      self.method = ole_object.Methods_ (method_name)
      self.qualifiers = {}
      for q in self.method.Qualifiers_:
        self.qualifiers[q.Name] = q.Value
      self.provenance = u"\n".join (self.qualifiers.get ("MappingStrings", []))

      self.in_parameters = self.method.InParameters
      self.out_parameters = self.method.OutParameters
      
      if self.in_parameters:
        self.in_parameter_names = parameter_names (self.in_parameters)
      else:
        self.in_parameter_names = []
        
      if self.out_parameters:
        self.out_parameter_names = parameter_names (self.out_parameters)
      else:
        self.out_parameter_names = []
      
      doc = u"%s (%s) => (%s)" % (
        method_name,
        ", ".join ([name + " " + datatype + (u"", u"[]")[is_array] for (name, is_array, datatype, bitmap) in self.in_parameter_names]),
        ", ".join ([name + " " + datatype + (u"", u"[]")[is_array] for (name, is_array, datatype, bitmap) in self.out_parameter_names])
      )
      privileges = self.qualifiers.get ("Privileges", [])
      if privileges:
        doc += u" | Needs: " + ", ".join (privileges)
      self.__doc__ = doc
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def __call__ (self, *args, **kwargs):
    ur"""Execute the call to a WMI method, returning
    a tuple (even if is of only one value) containing
    the out and return parameters.
    """
    try:
      if self.in_parameters:
        parameter_names = {}
        for name, is_array, datatype, bitmap in self.in_parameter_names:
          parameter_names[name] = is_array

        parameters = self.in_parameters
        
        #
        # Check positional parameters first
        #
        for n_arg in range (len (args)):
          arg = args[n_arg]
          parameter = parameters.Properties_[n_arg]
          if parameter.IsArray:
            try: list (arg)
            except TypeError: raise TypeError, u"parameter %d must be iterable" % n_arg
          parameter.Value = arg

        #
        # If any keyword param supersedes a positional one,
        # it'll simply overwrite it.
        #
        for k, v in kwargs.items ():
          is_array = parameter_names.get (k)
          if is_array is None:
            raise AttributeError, u"%s is not a valid parameter for %s" % (k, self.__doc__)
          else:
            if is_array:
              try: list (v)
              except TypeError: raise TypeError, u"%s must be iterable" % k
          parameters.Properties_ (k).Value = v

        result = self.ole_object.ExecMethod_ (self.method.Name, self.in_parameters)
      else:
        result = self.ole_object.ExecMethod_ (self.method.Name)

      results = []
      for name, is_array, datatype, bitmap in self.out_parameter_names:
        value = result.Properties_ (name).Value
        if is_array:
          #
          # Thanks to Jonas Bjering for bug report and patch
          #
          results.append (list (value or []))
        else:
          results.append (value)
      return tuple (results)

    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def __repr__ (self):
    return "<function %s>" % self.__doc__

#
# class _wmi_object
#
class _wmi_object (object):
  ur"""This class is the heart of the WMI functionality exposed; it
  keeps track of an object's properties and methods, for WMI classes
  and WMI instances, and hands off in the right direction. The user
  won't usually need to know what's happening here: simply use the
  attributes or methods.
  
  The underlying WMI object is available as :const:`ole_object` although
  unknown attribute attempts are passed straight through to it in any case.
  
  Key information:
  
  * Equality uses the WMI :const:`CompareTo` method
  * The string representation uses the WMI :const:`GetObjectText\_`
  * Setting attributes will set the properties of the underlying object,
    calling :meth:`put` if the object comes from the database (and
    not if it's the result of a call to :meth:`new`. To set several
    attributes at once without calling :meth:`put` for each one,
    use :meth:`set`.
  """

  def __init__ (self, ole_object, instance_of=None, fields=[], property_map={}):
    try:
      _set (self, "ole_object", ole_object)
      _set (self, "_instance_of", instance_of)
      _set (self, "properties", {})
      _set (self, "methods", {})
      _set (self, "_associated_classes", None)
      _set (self, "property_map", property_map)
      _set (self, "_keys", None)
      if instance_of:
        _set (self, "_namespace", instance_of._namespace)

      if fields:
        for field in fields:
          self.properties[field] = None
      else:
        for p in ole_object.Properties_:
          self.properties[p.Name] = None

      for m in ole_object.Methods_:
        self.methods[m.Name] = None

      _set (self, "_properties", self.properties.keys ())
      _set (self, "_methods", self.methods.keys ())

      _set (self, "qualifiers", {})
      for q in self.ole_object.Qualifiers_:
        self.qualifiers[q.Name] = q.Value
      _set (self, "is_association", self.qualifiers.has_key ("Association"))

    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def __str__ (self):
    ur"""For a call to print [object] return the OLE description
    of the properties / values of the object
    """
    try:
      return self.ole_object.GetObjectText_ ()
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def __repr__ (self):
    ur"""Indicate both the fact that this is a wrapped WMI object
    and the WMI object's own identifying class.
    """
    try:
      return u"<%s: %s>" % (self.__class__.__name__, str (self.Path_.Path))
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def _cached_properties (self, attribute):
    if self.properties[attribute] is None:
      self.properties[attribute] = self.ole_object.Properties_ (attribute)
    return self.properties[attribute]

  def _cached_methods (self, attribute):
    if self.methods[attribute] is None:
      self.methods[attribute] = _wmi_method (self.ole_object, attribute)
    return self.methods[attribute]

  def __getattr__ (self, attribute):
    ur"""Attempt to pass attribute calls to the proxied COM object.
    If the attribute is recognised as a property, return its value;
    if it is recognised as a method, return a method wrapper which
    can then be called with parameters; otherwise pass the lookup
    on to the underlying object.
    """
    try:
      if self.properties.has_key (attribute):
        factory = self.property_map.get (attribute, lambda x: x)
        value = factory (self._cached_properties (attribute).Value)
        #
        # If this is an association, its properties are
        #  actually the paths to the two aspects of the
        #  association, so translate them automatically
        #  into WMI objects.
        #
        if self.is_association:
          return WMI (moniker=value)
        else:
          return value
      elif attribute in self.methods:
        return self._cached_methods (attribute)
      #~ elif attribute in self.associated_classes:
        #~ return _wmi_associator (self, self.associated_classes[attribute])
      else:
        return getattr (self.ole_object, attribute)
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def __setattr__ (self, attribute, value):
    ur"""If the attribute to be set is valid for the proxied
    COM object, set that objects's parameter value; if not,
    raise an exception.
    """
    try:
      if self.properties.has_key (attribute):
        self._cached_properties (attribute).Value = value
        if self.ole_object.Path_.Path:
          self.ole_object.Put_ ()
      else:
        raise AttributeError, attribute
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def __eq__ (self, other):
    ur"""Use WMI's CompareTo_ to compare this object with
    another. Don't try to do anything if the other
    object is not a wmi object. It might be possible
    to compare this object's unique key with a string
    or something, but this doesn't seem to be universal
    enough to merit a special case.
    """
    if isinstance (other, self.__class__):
      return self.ole_object.CompareTo_ (other.ole_object)
    else:
      raise x_wmi, u"Can't compare a WMI object with something else"

  def _getAttributeNames (self):
     ur"""Return list of methods/properties for IPython completion"""
     attribs = [str (x) for x in self.methods.keys ()] 
     attribs.extend ([str (x) for x in self.properties.keys ()])
     return attribs
  
  def put (self):
    ur"""Force the current attributes into the underlying object. This
    here for completeness rather than otherwise; the :meth:`__setattr__`
    and :meth:`set` functionality already call :const:`Put\_` where
    appropriate.
    """
    self.ole_object.Put_ ()

  def get_keys (self):
    ur"""A WMI object is uniquely defined by a set of properties
    which constitute its keys. Lazily retrieves the keys for this
    instance or class.
    
    :returns: list of key property names
    """
    if self._keys is None:
      _set (self, "_keys", [])
      for property in self.ole_object.Properties_:
        for qualifier in property.Qualifiers_:
          if qualifier.Name == "key" and qualifier.Value:
            self._keys.append (property.Name)
    return self._keys
  keys = property (get_keys)

  def set (self, **kwargs):
    ur"""Set several properties of the underlying object
    at one go. This is particularly useful in combination
    with the new () method below. However, an instance
    which has been spawned in this way won't have enough
    information to write pack, so only try if the
    instance has a path.
    """
    if kwargs:
      try:
        for attribute, value in kwargs.items ():
          if self.properties.has_key (attribute):
            self._cached_properties (attribute).Value = value
          else:
            raise AttributeError, attribute
        #
        # Only try to write the attributes
        #  back if the object exists.
        #
        if self.ole_object.Path_.Path:
          self.ole_object.Put_ ()
      except pywintypes.com_error, error_info:
        handle_com_error (error_info)

  def path (self):
    ur"""Return the WMI URI to this object. Can be used to
    determine the path relative to the parent namespace::

      pp0 = wmi.WMI ().Win32_ParallelPort ()[0]
      print pp0.path ().RelPath
    """
    try:
      return self.ole_object.Path_
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def derivation (self):
    ur"""Return a tuple representing the object derivation for
     this object, with the most specific object first::

      pp0 = wmi.WMI ().Win32_ParallelPort ()[0]
      print ' <- '.join (pp0.derivation ())
    """
    try:
      return self.ole_object.Derivation_
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def _cached_associated_classes (self):
    if self._associated_classes is None:
      if isinstance (self, _wmi_class):
        params = {'bSchemaOnly' : True}
      else:
        params = {'bClassesOnly' : True}
      try:
        associated_classes = dict ((assoc.Path_.Class, _wmi_class (self._namespace, assoc)) for assoc in self.ole_object.Associators_ (**params))
        _set (self, "_associated_classes", associated_classes)
      except pywintypes.com_error, error_info:
        handle_com_error (error_info)
        
    return self._associated_classes
  associated_classes = property (_cached_associated_classes)

  def associators (self, wmi_association_class="", wmi_result_class=""):
    ur"""Return a list of objects related to this one, optionally limited
    either by association class (ie the name of the class which relates
    them) or by result class (ie the name of the class which would be
    retrieved)::

      c = wmi.WMI ()
      pp = c.Win32_ParallelPort ()[0]

      for i in pp.associators (wmi_association_class="Win32_PortResource"):
        print i

      for i in pp.associators (wmi_result_class="Win32_PnPEntity"):
        print i
    """
    try:
      return [
        _wmi_object (i) for i in \
          self.ole_object.Associators_ (
           strAssocClass=wmi_association_class,
           strResultClass=wmi_result_class
         )
      ]
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def referenced_classes (self, wmi_class=""):
    ur"""Return a list of class which are references by this. This was
    put in place originally to support the wmiweb.py program which
    provides user visibility of WMI class structures.
    
    :returns: list of :class:`_wmi_class` objects referenced by this
    """
    namespace = getattr (self, "_namespace", getattr (self._instance_of, "_namespace", None))
    params = {
      "strResultClass" : wmi_class
    }
    if isinstance (self, _wmi_class):
      params['bSchemaOnly'] = True
    else:
      params['bClassesOnly'] = True
    try:
      return [_wmi_class (namespace, i) for i in self.ole_object.References_ (**params)]
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def references (self, wmi_class=""):
    ur"""Return a list of associations involving this object, optionally
    limited by the result class (the name of the association class).

    NB Associations are treated specially; although WMI only returns
    the string corresponding to the instance of each associated object,
    this module will automatically convert that to the object itself::

      c =  wmi.WMI ()
      sp = c.Win32_SerialPort ()[0]

      for i in sp.references ():
        print i

      for i in sp.references (wmi_class="Win32_SerialPortSetting"):
        print i
    """
    try:
      return [_wmi_object (i) for i in self.ole_object.References_ (strResultClass=wmi_class)]
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

#
# class _wmi_event
#
class _wmi_event (_wmi_object):
  ur"""Slight extension of the _wmi_object class to allow
  objects which are the result of events firing to return
  extra information such as the type of event.
  """
  event_type_re = re.compile ("__Instance(Creation|Modification|Deletion)Event")
  def __init__ (self, event, event_info):
    _wmi_object.__init__ (self, event)
    _set (self, "event_type", None)
    _set (self, "timestamp", None)
    _set (self, "previous", None)
    
    if event_info:
      event_type = self.event_type_re.match (event_info.Path_.Class).group (1).lower ()
      _set (self, "event_type", event_type)
      if hasattr (event_info, "TIME_CREATED"):
        _set (self, "timestamp", from_1601 (event_info.TIME_CREATED))
      if hasattr (event_info, "PreviousInstance"):
        _set (self, "previous", event_info.PreviousInstance)

#
# class _wmi_class
#
class _wmi_class (_wmi_object):
  ur"""Currying class to assist in issuing queries against
  a WMI namespace. The idea is that when someone issues
  an otherwise unknown method against the WMI object, if
  it matches a known WMI class a query object will be
  returned which may then be called with one or more params
  which will form the WHERE clause::

    c = wmi.WMI ()
    c_drive = c.Win32_LogicalDisk (Name='C:')
  """
  def __init__ (self, namespace, wmi_class):
    _wmi_object.__init__ (self, wmi_class)
    _set (self, "_class_name", wmi_class.Path_.Class)
    if namespace:
      _set (self, "_namespace", namespace)
    else:
      class_moniker = wmi_class.Path_.DisplayName
      winmgmts, namespace_moniker, class_name = class_moniker.split (":")
      namespace = _wmi_namespace (GetObject (winmgmts + ":" + namespace_moniker))
      _set (self, "_namespace", namespace)

  def query (self, fields=[], **where_clause):
    ur"""Make it slightly easier to query against the class,
    by calling the namespace's query with the class preset.
    Won't work if the class has been instantiated directly.
    """
    if self._namespace is None:
      raise x_wmi_no_namespace, u"You cannot query directly from a WMI class"

    try:
      field_list = u", ".join (fields) or u"*"
      wql = u"SELECT " + field_list + u" FROM " + self._class_name
      if where_clause:
        wql += u" WHERE " + u" AND ". join ([u"%s = '%s'" % (k, v) for k, v in where_clause.items ()])
      return self._namespace.query (wql, self, fields)
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  __call__ = query

  def watch_for (
    self,
    notification_type=u"operation",
    delay_secs=1,
    fields=[],
    **where_clause
  ):
    if self._namespace is None:
      raise x_wmi_no_namespace, u"You cannot watch directly from a WMI class"

    return self._namespace.watch_for (
      notification_type=notification_type,
      wmi_class=self,
      delay_secs=delay_secs,
      fields=fields,
      **where_clause
    )

  def instances (self):
    ur"""Return a list of instances of the WMI class. 
    Equivalent to::
    
      wmi.WMI ().Win32_Process ()
    """
    try:
      return [_wmi_object (instance, self) for instance in self.Instances_ ()]
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def new (self, **kwargs):
    ur"""This is the equivalent to the raw-WMI SpawnInstance\_
    method. Note that there are relatively few uses for
    this, certainly fewer than you might imagine. Most
    classes which need to create a new *real* instance
    of themselves, eg Win32_Process, offer a .Create
    method. SpawnInstance\_ is generally reserved for
    instances which are passed as parameters to such
    .Create methods, a common example being the
    Win32_SecurityDescriptor, passed to Win32_Share.Create
    and other instances which need security.

    The example here is Win32_ProcessStartup, which
    controls the shown/hidden state etc. of a new
    Win32_Process instance::

      import win32con
      import wmi
      c = wmi.WMI ()
      startup = c.Win32_ProcessStartup.new (ShowWindow=win32con.SW_SHOWMINIMIZED)
      pid, retval = c.Win32_Process.Create (
        CommandLine="notepad.exe",
        ProcessStartupInformation=startup
      )

    NB previous versions of this module, used this function
    to create new process. This is *not* a good example
    of its use; it is better handled with something like
    the example above.
    """
    try:
      obj = _wmi_object (self.SpawnInstance_ (), self)
      obj.set (**kwargs)
      return obj
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

#
# class _wmi_result
#
class _wmi_result (object):
  ur"""Simple, data only result for targeted WMI queries which request
  data only result classes via :meth:`_wmi_namespace.fetch_as_classes`.
  """
  def __init__(self, obj, attributes):
    if attributes:
      for attr in attributes:
        self.__dict__[attr] = obj.Properties_ (attr).Value
    else:
      for p in obj.Properties_:
        attr = p.Name
        self.__dict__[attr] = obj.Properties_(attr).Value

#
# class WMI
#
class _wmi_namespace (object):
  ur"""A WMI root of a computer system. The classes attribute holds a list
  of the classes on offer. This means you can explore a bit with
  things like this::

    c = wmi.WMI ()
    for i in c.classes:
      if "user" in i.lower ():
        print i
  """
  def __init__ (self, namespace, find_classes=None):
    _set (self, "_namespace", namespace)
    #
    # wmi attribute preserved for backwards compatibility
    #
    _set (self, "wmi", namespace)

    # Initialise the "classes" attribute, to avoid infinite recursion in the
    # __getattr__ method (which uses it).
    self._classes_map = {}
    self._classes = None

  def __repr__ (self):
    return u"<_wmi_namespace: %s>" % self.wmi

  def __str__ (self):
    return repr (self)

  def get (self, moniker):
    try:
      return _wmi_object (self.wmi.Get (moniker))
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def handle (self):
    """The raw OLE object representing the WMI namespace"""
    return self._namespace

  def get_classes (self):
    ur"""Determine -- and cache -- what classes are defined within this namespace
    """
    if self._classes is None:
      try:
        self._classes = self.subclasses_of ().keys ()
      except AttributeError:
        pass
    return self._classes
  classes = property (get_classes)
  
  def subclasses_of (self, root="", regex=r".*"):
    ur"""Find classes within this namespace underneath a root class 
    which match a regular expression (by default, match everything).
    If no root class is given, all matching classes are returned::
    
      c = wmi.WMI ()
      tcp_perf_classes = c.subclasses_of ("Win32_PerfFormattedData", ".*Tcp.*")
      
    :param root: (optional) name of root class to search under
    :param regex: (optional) regex to filter matching classes
    :returns: dictionary mapping matching class name to :const:`None`
    """
    classes = {}
    for c in self._namespace.SubclassesOf (root):
      klass = c.Path_.Class
      if re.match (regex, klass):
        classes[klass] = None
    return classes

  def instances (self, class_name):
    ur"""Return a list of instances of the WMI class.
    """
    try:
      return [_wmi_object (obj) for obj in self._namespace.InstancesOf (class_name)]
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def new (self, wmi_class, **kwargs):
    """This is now implemented by a call to _wmi_namespace.new (qv)"""
    return getattr (self, wmi_class).new (**kwargs)

  new_instance_of = new

  def _raw_query (self, wql):
    ur"""Execute a WQL query and return its raw results.  Use the flags
    recommended by Microsoft to achieve a read-only, semi-synchronous
    query where the time is taken while looping through. Should really
    be a generator, but ...
    NB Backslashes need to be doubled up.
    """
    flags = wbemFlagReturnImmediately | wbemFlagForwardOnly
    wql = wql.replace (u"\\", u"\\\\")
    if _DEBUG: print u"_raw_query(wql):", wql
    try:
      return self._namespace.ExecQuery (strQuery=wql, iFlags=flags)
    except pywintypes.com_error, (hresult, hresult_text, additional, param_in_error):
      raise WMI_EXCEPTIONS.get (hresult, x_wmi (hresult))

  def query (self, wql, instance_of=None, fields=[]):
    ur"""Perform an arbitrary query against a WMI object, and return
    a list of _wmi_object representations of the results.
    """
    return [ _wmi_object (obj, instance_of, fields) for obj in self._raw_query(wql) ]

  def fetch_as_classes (self, wmi_classname, fields=(), **where_clause):
    ur"""Build and execute a wql query to fetch the specified list of fields from
    the specified wmi_classname + where_clause, then return the results as
    a list of simple class instances with attributes matching fields_list.

    If fields is left empty, select * and pre-load all class attributes for
    each class returned.
    """
    wql = u"SELECT %s FROM %s" % (fields and u", ".join (fields) or u"*", wmi_classname)
    if where_clause:
      wql += u" WHERE " + u" AND ".join ([u"%s = '%s'" % (k, v) for k, v in where_clause.items()])
    return [_wmi_result (obj, fields) for obj in self._raw_query(wql)]

  def fetch_as_lists (self, wmi_classname, fields, **where_clause):
    ur"""Build and execute a wql query to fetch the specified list of fields from
    the specified wmi_classname + where_clause, then return the results as
    a list of lists whose values correspond fields_list.
    """
    wql = u"SELECT %s FROM %s" % (u", ".join (fields), wmi_classname)
    if where_clause:
      wql += u" WHERE " + u" AND ".join ([u"%s = '%s'" % (k, v) for k, v in where_clause.items()])
    results = []
    for obj in self._raw_query(wql):
        results.append ([obj.Properties_ (field).Value for field in fields])
    return results

  def watch_for (
    self,
    raw_wql=None,
    notification_type=u"operation",
    wmi_class=None,
    delay_secs=1,
    fields=[],
    **where_clause
  ):
    ur"""Set up an event tracker on a WMI event. This function
    returns an wmi_watcher which can be called to get the
    next event::

      c = wmi.WMI ()
      
      raw_wql = "SELECT * FROM __InstanceCreationEvent WITHIN 2 WHERE TargetInstance ISA 'Win32_Process'"
      watcher = c.watch_for (raw_wql=raw_wql)
      while 1:
        process_created = watcher ()
        print process_created.Name

    or::
     
      watcher = c.watch_for (
        notification_type="Creation",
        wmi_class="Win32_Process",
        delay_secs=2,
        Name='calc.exe'
      )
      calc_created = watcher ()

    Now supports timeout on the call to watcher::

      import pythoncom
      import wmi
      c = wmi.WMI (privileges=["Security"])
      watcher1 = c.watch_for (
        notification_type="Creation",
        wmi_class="Win32_NTLogEvent",
        Type="error"
      )
      watcher2 = c.watch_for (
        notification_type="Creation",
        wmi_class="Win32_NTLogEvent",
        Type="warning"
      )

      while 1:
        try:
          error_log = watcher1 (500)
        except wmi.x_wmi_timed_out:
          pythoncom.PumpWaitingMessages ()
        else:
          print error_log

        try:
          warning_log = watcher2 (500)
        except wmi.x_wmi_timed_out:
          pythoncom.PumpWaitingMessages ()
        else:
          print warning_log
    """
    if wmi_class:
      if isinstance (wmi_class, _wmi_class):
        class_name = wmi_class._class_name
      else:
        class_name = wmi_class
        wmi_class = getattr (self, class_name)
      is_extrinsic = u"__ExtrinsicEvent" in wmi_class.derivation ()
    else:
      class_name = is_extrinsic = None
    if raw_wql:
      wql = raw_wql
    else:
      field_list = u", ".join (fields) or u"*"
      if is_extrinsic:
        if where_clause:
          where = u" WHERE " + u" AND ".join ([u"%s = '%s'" % (k, v) for k, v in where_clause.items ()])
        else:
          where = u""
        wql = u"SELECT " + field_list + " FROM " + class_name + where
      else:
        if where_clause:
          where = u" AND " + u" AND ".join ([u"TargetInstance.%s = '%s'" % (k, v) for k, v in where_clause.items ()])
        else:
          where = u""
        wql = \
          u"SELECT %s FROM __Instance%sEvent WITHIN %d WHERE TargetInstance ISA '%s' %s" % \
          (field_list, notification_type, delay_secs, class_name, where)

      if _DEBUG: print wql

    try:
      return _wmi_watcher (self._namespace.ExecNotificationQuery (wql), is_extrinsic=is_extrinsic)
    except pywintypes.com_error, error_info:
      handle_com_error (error_info)

  def __getattr__ (self, attribute):
    ur"""Offer WMI classes as simple attributes. Pass through any untrapped 
    unattribute to the underlying OLE object. This means that new or 
    unmapped functionality is still available to the module user.
    """
    #
    # Don't try to match against known classes as was previously
    # done since the list may not have been requested 
    # (find_classes=False).
    #
    try:
      return self._cached_classes (attribute)
    except pywintypes.com_error, error_info:
      try:
        return self._cached_classes ("Win32_" + attribute)
      except pywintypes.com_error, error_info:
        return getattr (self._namespace, attribute)

  def _cached_classes (self, class_name):
    ur"""Standard caching helper which keeps track of classes
    already retrieved by name and returns the existing object
    if found. If this is the first retrieval, store it and
    pass it back
    """
    if self._classes_map.get (class_name) is None:
      self._classes_map[class_name] = _wmi_class (self, self._namespace.Get (class_name))
    return self._classes_map[class_name]

  def _getAttributeNames (self):
    ur"""Return list of classes for IPython completion engine"""
    classes = [str (x) for x in self.classes if not x.startswith ('__')]
    return classes

#
# class _wmi_watcher
#
class _wmi_watcher (object):
  """Helper class for :meth:`_wmi_object.watch_for`"""

  _event_property_map = {
    "TargetInstance" : _wmi_object,
    "PreviousInstance" : _wmi_object
  }
  def __init__ (self, wmi_event, is_extrinsic):
    self.wmi_event = wmi_event
    self.is_extrinsic = is_extrinsic

  def __call__ (self, timeout_ms=-1):
    ur"""When called, return the instance which caused the event. Supports
    timeout in milliseconds (defaulting to infinite). If the watcher
    times out, x_wmi_timed_out is raised. This makes it easy to support
    watching for multiple objects.
    """
    try:
      event = self.wmi_event.NextEvent (timeout_ms)
      if self.is_extrinsic:
        return _wmi_event (event, None)
      else:
        return _wmi_event (
          event.Properties_ ("TargetInstance").Value,
          _wmi_object (event, property_map=self._event_property_map)
        )
    except pywintypes.com_error, error_info:
      hresult_code, hresult_name, additional_info, parameter_in_error = error_info
      if additional_info:
        wcode, source_of_error, error_description, whlp_file, whlp_context, scode = additional_info
        if scode == wbemErrTimedout:
          raise x_wmi_timed_out
      handle_com_error (error_info)

class _wmi_associator (object):
  ur"""(EXPERIMENTAL)
  """  
  def __init__ (self, originating_class, associated_class):
    print originating_class
    print associated_class
    
  def __call__ (self, *args, **kwargs):
    pass
    

PROTOCOL = "winmgmts:"
IMPERSONATION_LEVEL = "impersonate"
AUTHENTICATION_LEVEL = "default"
NAMESPACE = "root/cimv2"
def WMI (
  computer=".",
  impersonation_level="",
  authentication_level="",
  authority="",
  privileges="",
  moniker="",
  wmi=None,
  namespace="",
  suffix="",
  user="",
  password="",
  find_classes=True,
  debug=False
):
  ur"""The WMI constructor can either take a ready-made moniker or as many
  parts of one as are necessary::

    c = wmi.WMI (moniker="winmgmts:{impersonationLevel=Delegate}//remote")

  or::

    c = wmi.WMI (computer="remote", privileges=["!RemoteShutdown", "Security"])

  I daren't link to a Microsoft URL; they change so often. Try Googling for
  WMI construct moniker and see what it comes back with.

  For complete control, a named argument "wmi" can be supplied, which
  should be a SWbemServices object, which you create yourself::

    loc = win32com.client.Dispatch("WbemScripting.SWbemLocator")
    svc = loc.ConnectServer(...)
    c = wmi.WMI(wmi=svc)

  This is the only way of connecting to a remote computer with a different
  username, as the moniker syntax does not allow specification of a user
  name.

  If the "wmi" parameter is supplied, all other parameters are ignored.
  """
  global _DEBUG
  _DEBUG = debug

  #
  # If namespace is a blank string, leave
  # it unaltered as it might to trying to
  # access the root namespace
  #
  #if namespace is None:
  #  namespace = NAMESPACE

  try:
    if wmi:
      obj = wmi

    elif moniker:
      if not moniker.startswith (PROTOCOL):
        moniker = PROTOCOL + moniker
      if _DEBUG: print moniker
      obj = GetObject (moniker)

    else:
      if namespace:
        parts = re.split (r"[/\\]", namespace)
        if parts[0] != 'root':
          parts.insert (0, "root")
        namespace = "/".join (parts)
      
      if user:
        if impersonation_level or authentication_level or privileges or suffix:
          raise x_wmi, u"You can't specify an impersonation, authentication or privilege as well as a username"
        else:
          obj = connect_server (
            server=computer,
            namespace=namespace,
            user=user,
            password=password,
            authority=authority
          )

      else:
        moniker = construct_moniker (
          computer=computer,
          impersonation_level=impersonation_level or IMPERSONATION_LEVEL,
          authentication_level=authentication_level or AUTHENTICATION_LEVEL,
          authority=authority,
          privileges=privileges,
          namespace=namespace,
          suffix=suffix
        )
        if _DEBUG: print moniker
        obj = GetObject (moniker)

    return wmi_object (obj)

  except pywintypes.com_error, error_info:
    handle_com_error (error_info)

def construct_moniker (
  computer=None,
  impersonation_level="Impersonate",
  authentication_level="Default",
  authority=None,
  privileges=None,
  namespace=None,
  suffix=None
):
  security = []
  if impersonation_level: security.append ("impersonationLevel=%s" % impersonation_level)
  if authentication_level: security.append ("authenticationLevel=%s" % authentication_level)
  #
  # Use of the authority descriptor is invalid on the local machine
  #
  if authority and computer: security.append ("authority=%s" % authority)
  if privileges: security.append ("(%s)" % ", ".join (privileges))

  moniker = [PROTOCOL]
  if security: moniker.append ("{%s}/" % ",".join (security))
  if computer: moniker.append ("/%s/" % computer)
  if namespace:
    moniker.append (namespace)
  if suffix: moniker.append (":%s" % suffix)
  return "".join (moniker)

def wmi_object (obj):
  ur"""Attempt to return the appropriate class for a raw WMI object.
  If it doesn't have a path, it's a namespace. If the path is a class
  path it's a class, otherwise it's an instance.
  """
  try:
    path = obj.Path_
  except AttributeError:
    return _wmi_namespace (obj)
  else:
    if path.IsClass:
      return _wmi_class (None, obj)
    else:
      return _wmi_object (obj)

def connect_server (
  server,
  namespace = "",
  user = "",
  password = "",
  locale = "",
  authority = "",
  security_flags = 0,
  named_value_set = None
):
  ur"""Return a remote server running WMI

  :param server: name of the server
  :namespace: namespace to connect to: defaults to whatever's defined as default
  :user: username to connect as, either local or domain (dom\name or user@domain for XP)
  :password: leave blank to use current context
  :locale: desired locale in form MS_XXXX (eg MS_409 for Am En)
  :authority: either "Kerberos:" or an NT domain. Not needed if included in user
  :security_flags: if 0, connect will wait forever; if 0x80, connect will timeout at 2 mins
  :named_value_set: typically empty, otherwise a context-specific SWbemNamedValueSet
  
  Example::

    c = wmi.WMI (wmi=wmi.connect_server (server="remote_machine", user="myname", password="mypassword"))
  """
  if _DEBUG:
    print server
    print namespace
    print user
    print password
    print locale
    print authority
    print security_flags
    print named_value_set

  return Dispatch ("WbemScripting.SWbemLocator").\
    ConnectServer (
      server,
      namespace,
      user,
      password,
      locale,
      authority,
      security_flags,
      named_value_set
    )

def Registry (
  computer=None,
  impersonation_level="Impersonate",
  authentication_level="Default",
  authority=None,
  privileges=None,
  moniker=None
):
  ur"""Redundant convenience function to attach to a registry. This is
  exactly the same as::
  
    registry = wmi.WMI (namespace="root/default").StdRegProv
  """

  if not moniker:
    moniker = construct_moniker (
      computer=computer,
      impersonation_level=impersonation_level,
      authentication_level=authentication_level,
      authority=authority,
      privileges=privileges,
      namespace="root/default",
      suffix="StdRegProv"
    )

  try:
    return _wmi_object (GetObject (moniker))
  except pywintypes.com_error, error_info:
    handle_com_error (error_info)

#
# From a post to python-win32 by Sean
#
def machines_in_domain (domain_name):
  adsi = Dispatch ("ADsNameSpaces")
  nt = adsi.GetObject ("","WinNT:")
  result = nt.OpenDSObject ("WinNT://%s" % domain_name, "", "", 0)
  result.Filter = ["computer"]
  domain = []
  for machine in result:
    domain.append (machine.Name)
  return domain

def walk_related_classes (wmi_class, level=0, visited=None):
  if visited is None: 
    visited = []
  yield wmi_class, level
  visited.append (wmi_class)
  for assoc_class in wmi_class.associated_classes ():
    if assoc_class not in visited:
      for related in walk_related_classes (assoc_class, level+1, visited):
        yield related

#
# Typical use test
#
if __name__ == '__main__':
  system = WMI ()
  for my_computer in system.Win32_ComputerSystem ():
    print u"Disks on", my_computer.Name
    for disk in system.Win32_LogicalDisk ():
      print disk.Caption, disk.Description, disk.ProviderName or ""

