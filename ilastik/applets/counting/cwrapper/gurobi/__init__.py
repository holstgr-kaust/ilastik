# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# Copyright 2011-2014, the ilastik developers

import os
currentdir = os.path.dirname(__file__) 
import ctypes
libraryname = "libgurobiwrapper.so"
dllname = "gurobiwrapper.dll"
HAS_GUROBI = False
paths = [os.path.dirname(os.path.abspath(__file__)) + os.path.sep, ""]
for path in paths:
    try:
        sofile = path + libraryname
        extlib = ctypes.cdll.LoadLibrary(sofile)
        HAS_GUROBI = True
    except:
        pass

    try:
        dllfile = path + dllname
        extlib = ctypes.cdll.LoadLibrary(dllfile)
        HAS_GUROBI = True
    except:
        pass


if not HAS_GUROBI:
    raise RuntimeError("No gurobi wrapper found")
