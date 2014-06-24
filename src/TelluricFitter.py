"""
Telluric Fitter "TelFit"
=====================================================
This module provides the 'TelluricFitter' class, used
to fit the telluric lines in data.

Usage:
  - Initialize fitter: fitter = TelluricFitter()
  - Define variables to fit: must provide a dictionary where
      the key is the name of the variable, and the value is
      the initial guess value for that variable.
      Example: fitter.FitVariable({"ch4": 1.6, "h2o": 45.0})
  - Edit values of constant parameters: similar to FitVariable,
      but the variables given here will not be fit. Useful for 
      settings things like the telescope pointing angle, temperature,
      and pressure, which will be very well-known.
      Example: fitter.AdjustValue({"angle": 50.6})
  - Set bounds on fitted variables (fitter.SetBounds): Give a dictionary
      where the key is the name of the variable, and the value is
      a list of size 2 of the form [lower_bound, upper_bound]
  - Import data (fitter.ImportData): Copy data as a class variable.
      Must be given as a DataStructures.xypoint instance
  - Perform the fit: (fitter.Fit):
      Returns a DataStructures.xypoint instance of the model. The 
      x-values in the returned array are the same as the data.
   - Optional: retrieve a new version of the data, which is 
      wavelength-calibrated using the telluric lines and with
      a potentially better continuum fit using
      data2 = fitter.data  


      

    This file is part of the TelFit program.

    TelFit is free software: you can redistribute it and/or modify
    it under the terms of the MIT license.

    TelFit is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

    You should have received a copy of the MIT license
    along with TelFit.  If not, see <http://opensource.org/licenses/MIT>.


"""


import matplotlib.pyplot as plt
import numpy
import sys
import os
import subprocess
import scipy
from scipy.interpolate import  UnivariateSpline
from scipy.optimize import leastsq, minimize, fminbound
from scipy.linalg import svd, diagsvd
from scipy import mat
import MakeModel
import DataStructures
import FittingUtilities



class TelluricFitter:
  def __init__(self, debug=False, debug_level=2):
    #Set up parameters
    self.parnames = ["pressure", "temperature", "angle", "resolution", "wavestart", "waveend",
                     "h2o", "co2", "o3", "n2o", "co", "ch4", "o2", "no",
                     "so2", "no2", "nh3", "hno3"]
    self.const_pars = [795.0, 273.0, 45.0, 50000.0, 2200.0, 2400.0,
                       50.0, 368.5, 3.9e-2, 0.32, 0.14, 1.8, 2.1e5, 1.1e-19,
                       1e-4, 1e-4, 1e-4, 5.6e-4]
    self.bounds = [[0.0, 1e30] for par in self.parnames]  #Basically just making sure everything is > 0
    self.fitting = [False]*len(self.parnames)
    
    #Latitude and altitude (to nearest km) of the observatory
    #  Defaults are for McDonald Observatory
    self.observatory = {"latitude": 30.6,
                        "altitude": 2.0}
    self.data = None
    self.resolution_bounds = [10000.0, 100000.0]

    homedir = os.environ['HOME']
    self.resolution_fit_mode="gauss"
    self.fit_primary = False
    self.fit_source = False
    self.adjust_wave = "model"
    self.first_iteration=True
    self.continuum_fit_order = 7
    self.wavelength_fit_order = 3
    self.debug = debug
    self.debug_level = debug_level   #Number from 1-5, with 5 being the most verbose
    self.Modeler = MakeModel.Modeler(debug=self.debug)
    self.parvals = [[] for i in range(len(self.parnames))]
    self.chisq_vals = []
    self.ignore = []
    self.shift = 0   #The wavelength shift to make the model and data align

    #Just open and close chisq_summary, to clear anything already there
    outfile = open("chisq_summary.dat", "w")
    outfile.close()


### -----------------------------------------------

    
  def DisplayVariables(self, fitonly=False):
    """
    Display the value of each of the parameters, and show whether it is being fit or not

    -fitonly:  bool variable. If true, it only shows the variables being fit. Otherwise,
               it shows all variables.
    """
    print "%.15s\tValue\t\tFitting?\tBounds" %("Parameter".ljust(15))
    print "-------------\t-----\t\t-----\t\t-----"
    for i in range(len(self.parnames)):
      if (fitonly and self.fitting[i]) or not fitonly:
        if len(self.bounds[i]) == 2:
          print "%.15s\t%.5E\t%s\t\t%g - %g" %(self.parnames[i].ljust(15), self.const_pars[i], self.fitting[i], self.bounds[i][0], self.bounds[i][1])
        else:
          print "%.15s\t%.5g\t\t%s" %(self.parnames[i].ljust(15), self.const_pars[i], self.fitting[i])



### -----------------------------------------------


  def FitVariable(self, vardict):
    """
    Add one or more variables to the list being fit. 

    - vardict:   a dictionary where the key is the parameter 
                 name and the value is the value of that parameter.
    """
    for par in vardict.keys():
      try:
        idx = self.parnames.index(par)
        self.const_pars[idx] = vardict[par]
        self.fitting[idx] = True
      except ValueError:
        print "Error! Bad parameter name given. Currently available are: "
        self.DisplayVariables()
        raise ValueError


### -----------------------------------------------

        
  
  def AdjustValue(self, vardict):
    """
    Similar to FitVariable, but this just adjusts the value of a constant parameter.
    Warning! If the variable will be removed from the fitting list, so DO NOT use this
    to adjust the value of a parameter you want fitted.
    """
    for par in vardict.keys():
      try:
        idx = self.parnames.index(par)
        self.const_pars[idx] = vardict[par]
        self.fitting[idx] = False
      except ValueError:
        print "Error! Bad parameter name given. Currently available are: "
        self.DisplayVariables()
        raise ValueError


### -----------------------------------------------


  def GetValue(self, variable):
    """
    Returns the value of the variable given.
    Useful to get the fitted value of the parameters
    """
    if variable in self.parnames:
      idx = self.parnames.index(variable)
      return self.const_pars[idx]
    else:
      print "Error! Bad parameter name given (%s)." %(variable)
      print "Currently available parameter names are: "
      self.DisplayVariables()


### -----------------------------------------------


  
  def SetBounds(self, bounddict):
    """
    Similar to FitVariable, but it sets bounds on the variable. This can technically
      be done for any variable, but is only useful to set bounds for those variables
      being fit (and detector resolution)
    """
    for par in bounddict.keys():
      try:
        idx = self.parnames.index(par)
        self.bounds[idx] = bounddict[par]
        if par == "resolution":
          self.resolution_bounds = bounddict[par]
      except ValueError:
        print "Error! Bad parameter name given. Currently available are: "
        self.DisplayVariables()
        raise ValueError


### -----------------------------------------------


  
  def SetObservatory(self, observatory):
    """
    Set the observatory. Can either give a dictionary with the latitude and altitude,
      or give the name of the observatory. Some names are hard-coded in here.
    """
    if type(observatory) == str:
      if observatory.lower() == "ctio":
        self.observatory["latitude"] = -30.6
        self.observatory["altitude"] = 2.2
      if observatory.lower() == "la silla":
        self.observatory["latitude"] = -29.3
        self.observatory["altitude"] = 2.4
      if observatory.lower() == "paranal":
        self.observatory["latitude"] = -24.6
        self.observatory["altitude"] = 2.6
      if observatory.lower() == "mauna kea":
        self.observatory["latitude"] = 19.8
        self.observatory["altitude"] = 4.2
      if observatory.lower() == "mcdonald":
        self.observatory["latitude"] = 30.7
        self.observatory["altitude"] = 2.1
        
    elif type(observatory) == dict:
      if "latitude" in observatory.keys() and "altitude" in observatory.keys():
        self.observatory = observatory
      else:
        print "Error! Wrong keys in observatory dictionary! Keys must be"
        print "'latitude' and 'altitude'. Yours are: ", observatory.keys()
        raise KeyError
    else:
      raise ValueError("Error! Unrecognized input to TelluricFitter.SetObservatory()")
    


### -----------------------------------------------

  
  def ImportData(self, data):
    """
    Function for the user to give the data. The data should be in the form of
      a DataStructures.xypoint structure.
    """
    if not isinstance(data, DataStructures.xypoint):
      raise TypeError( "ImportData Error! Given data is not a DataStructures.xypoint structure!" )
    self.data = data.copy()
    return


### -----------------------------------------------



  
  def EditAtmosphereProfile(self, profilename, profile_height, profile_value):
    """
    Edits the atmosphere profile for a given parameter. This is just a wrapper
      for the MakeModel.Modeler method, but the docstring is replicated below:

    -profilename:  A string with the name of the profile to edit.
                   Should be either 'pressure', 'temperature', or
                   one of the molecules given in the MakeModel.MoleculeNumbers
                   dictionary
    -profile_height:  A numpy array with the height in the atmosphere (in km)
    -profile_value:   A numpy array with the value of the profile parameter at
                      each height given in profile_height.
    """
    self.Modeler.EditProfile(profilename, profile_height, profile_value)
    


### -----------------------------------------------
  
  
  def IgnoreRegions(self, region):
    """
    Tells the fitter to ignore certain regions of the spectrum
      in the chi-squared calculation. Useful for stellar or interstellar
      lines.

    -region:  Can be either a list of size 2 with the beginning and ending
              wavelength range to ignore, or a list of lists giving several
              wavelength ranges at once. 
    """
    if not isinstance(region, list) or len(region) == 0:
      raise TypeError("Must give a non-empty list to TelluricFitter.IgnoreRegions")
    
    if isinstance(region[0], list):
      #The user gave a list of lists. Append each one to self.ignore
      for r in region:
        self.ignore.append(r)
    elif isinstance(region[0], int):
      #The user gave a single region. Append to self.ignore
      self.ignore.append(region)
    else:
      raise TypeError("Unrecognized variable type for region given in TelluricFitter.IgnoreRegions")
    
    return


### -----------------------------------------------
###         Main Fit Function!
### -----------------------------------------------
  
  def Fit(self, data=None, resolution_fit_mode="gauss", fit_primary=False, fit_source=False, return_resolution=False, adjust_wave="model", continuum_fit_order=7, wavelength_fit_order=3):
    """
    The main fitting function. Before calling this, the user MUST
      1: call FitVariable at least once, specifying which variables will be fit
      2: Set resolution bounds (any other bounds are optional)


    -data:                  If given, this should be a DataStructures.xypoint instance
                            giving the data you wish to fit. In previous versions, this
                            had to be given separately in the 'ImportData' method.
    
    -resolution_fit_mode:   controls which function is used to estimate the resolution.
                            "SVD" is for singlular value decomposition, while "gauss" 
                            is for convolving with a gaussian (and fitting the width 
                            of the guassian to give the best fit)                         

    -fit_source:            determines whether an iterative smoothing is applied to the 
                            data to approximate the source spectrum. Only works if the 
                            source spectrum has broad lines. If true, this function returns both
                            the best-fit model and the source estimate. 
    
    -return_resolution:     controls whether the best-fit resolution is returned to the user.
                            One case I have used this for is to fit echelle data of late-type 
                            stars by getting all the best-fit parameters from redder orders,
                            and then applying those atmospheric parameters to the rest of the
                            orders.

    -adjust_wave:           can be set to either 'data' or 'model'. To wavelength calibrate the 
                            data to the telluric lines, set to 'data'. If you think the wavelength
                            calibration is good on the data (such as Th-Ar lines in the optical), 
                            then set to 'model' Note that currently, the vacuum --> air conversion 
                            for the telluric model is done in a very approximate sense, so 
                            adjusting the data wavelengths may introduce a small (few km/s) offset 
                            from what it should be.
                            
    -continuum_fit_order:   The polynomial order with which to fit the continuum. It uses a 
                            sigma-clipping algorithm so that the continuum is not strongly 
                            affected by stellar lines (either absorption or emission)
                            
    -wavelength_fit_order:  The polynomial order with which to adjust the wavelength fit. Note
                            that the 'adjust_wave' input will determine whether the data or the
                            telluric model is wavelength-adjusted.
    """

    self.resolution_fit_mode=resolution_fit_mode
    self.fit_source = fit_primary
    self.fit_source = fit_source
    self.adjust_wave = adjust_wave
    self.continuum_fit_order = continuum_fit_order
    self.wavelength_fit_order = wavelength_fit_order
    self.return_resolution=return_resolution

    #Check if the user gave data to fit
    if data != None:
      self.ImportData(data)
    elif self.data == None:
      raise AttributeError ("\n\nError! Must supply data to fit\n\n!")


    #Make sure resolution bounds are given (resolution is always fit)
    idx = self.parnames.index("resolution")
    if len(self.bounds[idx]) < 2 and self.resolution_fit_mode != "SVD":
      print "Must give resolution bounds!"
      inp = raw_input("Enter the lowest and highest possible resolution, separated by a space: ")
      self.resolution_bounds = [float(inp.split()[0]), float(inp.split()[1])]


    #Make fitpars array
    fitpars = [self.const_pars[i] for i in range(len(self.parnames)) if self.fitting[i] ]
    if len(fitpars) < 1:
      print "\n\nError! Must fit at least one variable!\n\n"
      return
    

    #Set up the fitting logfile and logging arrays
    self.parvals = [[] for i in range(len(self.parnames))]
    self.chisq_vals = []
    outfile = open("chisq_summary.dat", "a")
    outfile.write("\n\n\n\n")
    for i in range(len(self.parnames)):
      if self.fitting[i]:
        outfile.write("%s\t" %self.parnames[i])
    outfile.write("X^2\n")
    outfile.close()


    #Perform the fit
    self.first_iteration = True
    errfcn = lambda pars: numpy.sum(self.FitErrorFunction(pars))
    bounds = [self.bounds[i] for i in range(len(self.parnames)) if self.fitting[i]]
    optdict = {"rhobeg": [1,5,1000.0]}
    optdict = {"eps": 5}
    fitpars, success = leastsq(self.FitErrorFunction, fitpars, diag=1.0/numpy.array(fitpars), epsfcn=0.001)

    #Save the best-fit values
    idx = 0
    for i in range(len(self.parnames)):
      if self.fitting[i]:
        self.const_pars[i] = fitpars[idx]
        idx += 1
        

    #Finally, return the best-fit model
    if self.fit_source:
      return self.GenerateModel(fitpars, separate_primary=True, return_resolution=return_resolution)
    else:
      return self.GenerateModel(fitpars, return_resolution=return_resolution)
    


### -----------------------------------------------


  
  def FitErrorFunction(self, fitpars):
    """
    The error function for the fitter. This should never be called directly!
    """
    if self.return_resolution:
      model, resolution = self.GenerateModel(fitpars, return_resolution=True)
    else:
      model = self.GenerateModel(fitpars)
    outfile = open("chisq_summary.dat", 'a')
    weights = 1.0 / self.data.err**2

    #Find the regions to use (ignoring the parts that were defined as bad)
    good = numpy.arange(self.data.x.size, dtype=numpy.int32)
    for region in self.ignore:
      x0 = min(region)
      x1 = max(region)
      tmp1 = [self.data.x[i] in self.data.x[good] for i in range(self.data.x.size)]
      tmp2 = numpy.logical_or(self.data.x<x0, self.data.x>x1)
      good = numpy.where(numpy.logical_and(tmp1, tmp2))[0]

    
    return_array = (self.data.y - self.data.cont*model.y)[good]**2 * weights[good]
    #Evaluate bound conditions and output the parameter value to the logfile.
    fit_idx = 0
    for i in range(len(self.bounds)):
      if self.fitting[i]:
        if len(self.bounds[i]) == 2:
          return_array += FittingUtilities.bound(self.bounds[i], fitpars[fit_idx])
        outfile.write("%.12g\t" %fitpars[fit_idx])
        self.parvals[i].append(fitpars[fit_idx])
        fit_idx += 1
      elif len(self.bounds[i]) == 2 and self.parnames[i] != "resolution":
        return_array += FittingUtilities.bound(self.bounds[i], self.const_pars[i])
    outfile.write("%g\n" %(numpy.sum(return_array)/float(weights.size)))
    
    self.chisq_vals.append(numpy.sum(return_array)/float(weights.size))
    print "X^2 = ", numpy.sum(return_array)/float(weights.size)
    outfile.close()
    
    return return_array




### -----------------------------------------------


  
  def GenerateModel(self, pars, nofit=False, separate_primary=False, return_resolution=False):
    """
    This function does the actual work of generating a model with the given parameters,
    fitting the continuum, making sure the model and data are well aligned in
    wavelength, and fitting the detector resolution. In general, it is not meant to be
    called directly by the user. However, the 'nofit' keyword turns this into a wrapper
    to MakeModel.Modeler().MakeModel() with all the appropriate parameters.

    """
    data = self.data
    #Update self.const_pars to include the new values in fitpars
    #  I know, it's confusing that const_pars holds some non-constant parameters...
    fit_idx = 0
    for i in range(len(self.parnames)):
      if self.fitting[i]:
        self.const_pars[i] = pars[fit_idx]
        fit_idx += 1
    self.DisplayVariables(fitonly=True)
    
    #Extract parameters from pars and const_pars. They will have variable
    #  names set from self.parnames
    fit_idx = 0
    for i in range(len(self.parnames)):
      #Assign to local variables by the parameter name
      if self.fitting[i]:
        exec("%s = %g" %(self.parnames[i], pars[fit_idx]))
        fit_idx += 1
      else:
        exec("%s = %g" %(self.parnames[i], self.const_pars[i]))
      
      #Make sure everything is within its bounds
      if len(self.bounds[i]) > 0:
        lower = self.bounds[i][0]
        upper = self.bounds[i][1]
        exec("%s = %g if %s < %g else %s" %(self.parnames[i], lower, self.parnames[i], lower, self.parnames[i]))
        exec("%s = %g if %s > %g else %s" %(self.parnames[i], upper, self.parnames[i], upper, self.parnames[i]))

    
    wavenum_start = 1e7/waveend
    wavenum_end = 1e7/wavestart
    lat = self.observatory["latitude"]
    alt = self.observatory["altitude"]
    

    #Generate the model:
    model = self.Modeler.MakeModel(pressure, temperature, wavenum_start, wavenum_end, angle, h2o, co2, o3, n2o, co, ch4, o2, no, so2, no2, nh3, hno3, lat=lat, alt=alt, wavegrid=None, resolution=None)
    
    #Shift the x-axis, using the shift from previous iterations
    if self.debug:
      print "Shifting by %.4g before fitting model" %self.shift
    if self.adjust_wave == "data":
      data.x += self.shift
    elif self.adjust_wave == "model":
      model.x -= self.shift

    #Save each model if debugging
    if self.debug and self.debug_level >= 5:
      FittingUtilities.ensure_dir("Models/")
      model_name = "Models/transmission"+"-%.2f" %pressure + "-%.2f" %temperature + "-%.1f" %h2o + "-%.1f" %angle + "-%.2f" %(co2) + "-%.2f" %(o3*100) + "-%.2f" %ch4 + "-%.2f" %(co*10)
      numpy.savetxt(model_name, numpy.transpose((model.x, model.y)), fmt="%.8f")
      
    #Interpolate to constant wavelength spacing
    xgrid = numpy.linspace(model.x[0], model.x[-1], model.x.size)
    model = FittingUtilities.RebinData(model, xgrid)

    #Use nofit if you want a model with reduced resolution. Probably easier
    #  to go through MakeModel directly though...
    if data == None or nofit:
      return FittingUtilities.ReduceResolution(model, resolution)

    

    model_original = model.copy()
  
    #Reduce to initial guess resolution
    if (resolution - 10 < self.resolution_bounds[0] or resolution+10 > self.resolution_bounds[1]):
      resolution = numpy.mean(self.resolution_bounds)
    model = FittingUtilities.ReduceResolution(model, resolution)
    model = FittingUtilities.RebinData(model, data.x)
    
    
    #Shift the data (or model) by a constant offset. This gets the wavelength calibration close
    shift = FittingUtilities.CCImprove(data, model, tol=0.1)
    if self.adjust_wave == "data":
      data.x += shift
    elif self.adjust_wave == "model":
      model_original.x -= shift
      # In this case, we need to adjust the resolution again
      model = FittingUtilities.ReduceResolution(model_original.copy(), resolution)
      model = FittingUtilities.RebinData(model, data.x)
    else:
      sys.exit("Error! adjust_wave parameter set to invalid value: %s" %self.adjust_wave)
    self.shift += shift

    
    resid = data.y/model.y
    nans = numpy.isnan(resid)
    resid[nans] = data.cont[nans]
      
    #As the model gets better, the continuum will be less affected by
    #  telluric lines, and so will get better
    data.cont = FittingUtilities.Continuum(data.x, resid, fitorder=self.continuum_fit_order, lowreject=2, highreject=3)
    
    if separate_primary or self.fit_source:
      print "Generating Primary star model"
      primary_star = data.copy()
      primary_star.y = FittingUtilities.Iterative_SV(resid/data.cont, 61, 4, lowreject=2, highreject=3)
      data.cont *= primary_star.y



    
    if self.debug and self.debug_level >= 4:
      print "Saving data and model arrays right before fitting the wavelength"
      print "  and resolution to Debug_Output1.log"
      numpy.savetxt("Debug_Output1.log", numpy.transpose((data.x, data.y, data.cont, model.x, model.y)))

      
    #Fine-tune the wavelength calibration by fitting the location of several telluric lines
    modelfcn, mean = self.FitWavelength(data, model.copy(), fitorder=self.wavelength_fit_order)
      
    if self.adjust_wave == "data":
      test = data.x - modelfcn(data.x - mean)
      xdiff = [test[j] - test[j-1] for j in range(1, len(test)-1)]
      if min(xdiff) > 0 and numpy.max(numpy.abs(test - data.x)) < 0.1 and min(test) > 0:
        print "Adjusting data wavelengths by at most %.8g nm" %numpy.max(test - model.x)
        data.x = test.copy()
      else:
        print "Warning! Wavelength calibration did not succeed!"
    elif self.adjust_wave == "model":
      test = model_original.x + modelfcn(model_original.x - mean)
      test2 = model.x + modelfcn(model.x - mean)
      xdiff = [test[j] - test[j-1] for j in range(1, len(test)-1)]
      if min(xdiff) > 0 and numpy.max(numpy.abs(test2 - model.x)) < 0.1 and min(test) > 0 and abs(test[0] - data.x[0]) < 50 and abs(test[-1] - data.x[-1]) < 50:
        print "Adjusting wavelength calibration by at most %.8g nm" %max(test2 - model.x)
        model_original.x = test.copy()
        model.x = test2.copy()
      else:
        print "Warning! Wavelength calibration did not succeed!"
        
    else:
      sys.exit("Error! adjust_wave set to an invalid value: %s" %self.adjust_wave)

    if self.debug and self.debug_level >= 4:
      print "Saving data and model arrays after fitting the wavelength"
      print "  and before the resolution fit to Debug_Output2.log"
      numpy.savetxt("Debug_Output2.log", numpy.transpose((data.x, data.y, data.cont, model.x, model.y)))

      
    #Fit instrumental resolution
    done = False
    while not done:
      done = True
      if "SVD" in self.resolution_fit_mode and min(model.y) < 0.95:
        model, self.broadstuff = self.Broaden(data.copy(), model_original.copy(), full_output=True)
      elif "gauss" in self.resolution_fit_mode:
        model, resolution = self.FitResolution(data.copy(), model_original.copy(), resolution)
      else:
        done = False
        print "Resolution fit mode set to an invalid value: %s" %self.resolution_fit_mode
        self.resolution_fit_mode = raw_input("Enter a valid mode (SVD or guass): ")
    
    
    self.data = data
    self.first_iteration = False
    if separate_primary:
      if return_resolution:
        return primary_star, model, resolution
      else:
        return primary_star, model
    else:
      data.cont /= primary_star.y
      if return_resolution:
        return model, resolution
      return model



### -----------------------------------------------
### Several functions for refining the wavelength calibration
### -----------------------------------------------
  
  def WavelengthErrorFunction(self, shift, data, model):
    """
    Error function for the scipy.minimize fitters. Not meant to be
    called directly by the user!
    """
    modelfcn = UnivariateSpline(model.x, model.y, s=0)
    weight = 1e9 * numpy.ones(data.x.size)
    weight[data.y > 0] = 1.0/numpy.sqrt(data.y[data.y > 0])
    weight[weight < 0.01] = 0.0
    newmodel = modelfcn(model.x + float(shift))
    if shift < 0:
      newmodel[model.x - float(shift) < model.x[0]] = 0
    else:
      newmodel[model.x - float(shift) > model.x[-1]] = 0
    returnvec = (data.y - newmodel)**2*weight
    return returnvec


### -----------------------------------------------


  def GaussianFitFunction(self, x,params):
    """
    Generate a gaussian absorption line. Not meant to be called
    directly by the user!
    """
    cont = params[0]
    depth = params[1]
    mu = params[2]
    sig = params[3]
    return cont - depth*numpy.exp(-(x-mu)**2/(2*sig**2))


### -----------------------------------------------


  def GaussianErrorFunction(self, params, x, y):
    """
    Error function for the scipy.minimize fitters. Not meant to be
    called directly by the user!
    """
    return self.GaussianFitFunction(x,params) - y


### -----------------------------------------------

  def FitGaussian(self, data):
    """
    This function fits a gaussian to a line. The input
    should be a small segment (~0.1 nm or so), in an xypoint structure
    Not meant to be called directly by the user!
    """
    cont = 1.0
    sig = 0.004
    minidx = numpy.argmin(data.y/data.cont)
    mu = data.x[minidx]
    depth = 1.0 - min(data.y/data.cont)
    pars = [cont, depth, mu, sig]
    pars, success = leastsq(self.GaussianErrorFunction, pars, args=(data.x, data.y/data.cont), diag=1.0/numpy.array(pars), epsfcn=1e-10)
    return pars, success


### -----------------------------------------------

  
  def FitWavelength(self, data_original, telluric, tol=0.05, oversampling=4, fitorder=3, numiters=10):
    """
    Function to fine-tune the wavelength solution of a generated model
      It does so by looking for telluric lines in both the
      data and the telluric model. For each line, it finds the shift needed
      to make them line up, and then fits a function to that fit over the
      full wavelength range of the data. Wavelength calibration MUST already 
      be very close for this algorithm to succeed! NOT MEANT TO BE CALLED
      DIRECTLY BY THE USER!
    """
    print "Fitting Wavelength"
    old = []
    new = []
    #Find lines in the telluric model
    linelist = FittingUtilities.FindLines(telluric, debug=self.debug, tol=0.995)
    if len(linelist) < fitorder:
      fit = lambda x: x
      mean = 0.0
      return fit, mean
    linelist = telluric.x[linelist]
    
    if self.debug and self.debug_level >= 5:
      logfilename = "FitWavelength.log"
      print "Outputting data and telluric model to %s" %logfilename
      numpy.savetxt(logfilename, numpy.transpose((data_original.x, data_original.y, data_original.cont, data_original.err)), fmt="%.8f")
      infile = open(logfilename, "a")
      infile.write("\n\n\n\n\n")
      numpy.savetxt(infile, numpy.transpose((telluric.x, telluric.y)), fmt="%.8f")
      infile.close()

    #Interpolate to finer spacing
    xgrid = numpy.linspace(data_original.x[0], data_original.x[-1], data_original.x.size*oversampling)
    data = FittingUtilities.RebinData(data_original, xgrid)
    model = FittingUtilities.RebinData(telluric, xgrid)
  
    #Begin loop over the lines
    numlines = 0
    model_lines = []
    dx = []
    for line in linelist:
      if line-tol > data.x[0] and line+tol < data.x[-1]:
        numlines += 1

        #Find line center in the model
        left = numpy.searchsorted(model.x, line - tol)
        right = numpy.searchsorted(model.x, line + tol)
        
        #Don't use lines that are saturated
        if min(model.y[left:right]) < 0.05:
          continue

        pars, model_success = self.FitGaussian(model[left:right])
        if model_success < 5 and pars[1] > 0 and pars[1] < 1:
          model_lines.append(pars[2])
        else:
          continue

        #Do the same for the data
        left = numpy.searchsorted(data.x, line - tol)
        right = numpy.searchsorted(data.x, line + tol)

        if min(data.y[left:right]/data.cont[left:right]) < 0.05:
          model_lines.pop()
          continue

        pars, data_success = self.FitGaussian(data[left:right])
        if data_success < 5 and pars[1] > 0 and pars[1] < 1:
          dx.append(pars[2] - model_lines[-1])
        else:
          model_lines.pop()

    #Convert the lists to numpy arrays        
    model_lines = numpy.array(model_lines)
    dx = numpy.array(dx)

    #Remove any points with very large shifts:
    badindices = numpy.where(numpy.abs(dx) > 0.015)[0]
    model_lines = numpy.delete(model_lines, badindices)
    dx = numpy.delete(dx, badindices)

    if self.debug and self.debug_level >= 5:
      plt.figure(2)
      plt.plot(model_lines, dx, 'ro')
      plt.title("Fitted Line shifts")
      plt.xlabel("Old Wavelength")
      plt.ylabel("New Wavelength")

    numlines = len(model_lines)
    print "Found %i lines in this order" %numlines
    fit = lambda x: x
    mean = 0.0
    if numlines < fitorder:
      return fit, mean
    
    #Check if there is a large gap between the telluric lines and the end of the order (can cause the fit to go crazy)
    keepfirst = False
    keeplast = False
    if min(model_lines) - data.x[0] > 1:
      model_lines = numpy.r_[data.x[0], model_lines]
      dx = numpy.r_[0.0, dx]
      keepfirst = True
    if data.x[-1] - max(model_lines) > 1:
      model_lines = numpy.r_[model_lines, data.x[-1]]
      dx = numpy.r_[dx, 0.0]
      keeplast = True
      
    #Iteratively fit with sigma-clipping
    done = False
    iternum = 0
    mean = numpy.mean(data.x)
    while not done and len(model_lines) >= fitorder and iternum < numiters:
      iternum += 1
      done = True
      print iternum, model_lines.size, dx.size
      fit = numpy.poly1d(numpy.polyfit(model_lines - mean, dx, fitorder))
      residuals = fit(model_lines - mean) - dx
      std = numpy.std(residuals)
      badindices = numpy.where(numpy.abs(residuals) > 3*std)[0]
      if 0 in badindices and keepfirst:
        idx = numpy.where(badindices == 0)[0]
        badindices = numpy.delete(badindices, idx)
      if data.size()-1 in badindices and keeplast:
        idx = numpy.where(badindices == data.size()-1)[0]
        badindices = numpy.delete(badindices, idx)
      if badindices.size > 0 and model_lines.size - badindices.size > 2*fitorder:
        done = False
        model_lines = numpy.delete(model_lines, badindices)
        dx = numpy.delete(dx, badindices)


    if self.debug and self.debug_level >= 5:
      plt.figure(3)
      plt.plot(model_lines, fit(model_lines - mean) - dx, 'ro')
      plt.title("Residuals")
      plt.xlabel("Wavelength")
      plt.ylabel("Delta-lambda")
      plt.show()
    
    return fit, mean


### -----------------------------------------------




  def Poly(self, pars, x):
    """
    Generates a polynomial with the given parameters
    for all of the x-values.
    x is assumed to be a numpy.ndarray!
     Not meant to be called directly by the user!
    """
    retval = numpy.zeros(x.size)
    for i in range(len(pars)):
      retval += pars[i]*x**i
    return retval


### -----------------------------------------------


  def WavelengthErrorFunctionNew(self, pars, data, model, maxdiff=0.05):
    """
    Cost function for the new wavelength fitter.
    Not meant to be called directly by the user!
    """
    dx = self.Poly(pars, data.x)
    penalty = numpy.sum(numpy.abs(dx[numpy.abs(dx) > maxdiff]))
    return (data.y/data.cont - model(data.x + dx))**2 + penalty



### -----------------------------------------------



  def FitWavelengthNew(self, data_original, telluric, fitorder=3):
    """
    This is a vastly simplified version of FitWavelength. 
    It takes the same inputs and returns the same thing,
    so is a drop-in replacement for the old FitWavelength.

    Instead of finding the lines, and generating a polynomial
    to apply to the axis as x --> f(x), it fits a polynomial
    to the delta-x. So, it fits the function for x --> x + f(x).
    This way, we can automatically penalize large deviations in 
    the wavelength.
    """
    modelfcn = UnivariateSpline(telluric.x, telluric.y, s=0)
    pars = numpy.zeros(fitorder + 1)
    output = leastsq(self.WavelengthErrorFunctionNew, pars, args=(data_original, modelfcn), full_output=True)
    pars = output[0]

    return lambda x: x - self.Poly(pars, x), 0


### -----------------------------------------------
###       Detector Resolution Fitter
### -----------------------------------------------

  
  def FitResolution(self, data, model, resolution=75000.0):
    """
    Fits the instrumental resolution with a Gaussian. This method is 
    called by GenerateModel, and is not meant to be called by the user!
    """
    
    print "Fitting Resolution"

    #Subsample the model to speed this part up (it doesn't affect the accuracy much)
    dx = (data.x[1] - data.x[0])/3.0
    xgrid = numpy.arange(model.x[0], model.x[-1]+dx, dx)
    #xgrid = numpy.linspace(model.x[0], model.x[-1], model.size()/5)
    newmodel = FittingUtilities.RebinData(model, xgrid)
 
    ResolutionFitErrorBrute = lambda resolution, data, model: numpy.sum(self.ResolutionFitError(resolution, data, model))
    
    resolution = fminbound(ResolutionFitErrorBrute, self.resolution_bounds[0], self.resolution_bounds[1], xtol=1, args=(data,newmodel))
    
    print "Optimal resolution found at R = ", float(resolution)
    newmodel = FittingUtilities.ReduceResolution(newmodel, float(resolution))
    return FittingUtilities.RebinData(newmodel, data.x), float(resolution)



### -----------------------------------------------
  
  
  def ResolutionFitError(self, resolution, data, model):
    """
    This function gets called by scipy.optimize.fminbound in FitResolution.
    Not meant to be called directly by the user!
    """
    resolution = max(1000.0, float(int(float(resolution) + 0.5)))
    if self.debug and self.debug_level >= 5:
      print "Saving inputs for R = ", resolution
      print " to Debug_ResFit.log and Debug_ResFit2.log"
      numpy.savetxt("Debug_ResFit.log", numpy.transpose((data.x, data.y, data.cont)))
      numpy.savetxt("Debug_Resfit2.log", numpy.transpose((model.x, model.y)))
    newmodel = FittingUtilities.ReduceResolution(model, resolution, extend=False)
    newmodel = FittingUtilities.RebinData(newmodel, data.x, synphot=False)

    #Find the regions to use (ignoring the parts that were defined as bad)
    good = numpy.arange(self.data.x.size, dtype=numpy.int32)
    for region in self.ignore:
      x0 = min(region)
      x1 = max(region)
      tmp1 = [self.data.x[i] in self.data.x[good] for i in range(self.data.x.size)]
      tmp2 = numpy.logical_or(self.data.x<x0, self.data.x>x1)
      good = numpy.where(numpy.logical_and(tmp1, tmp2))[0]

    weights = 1.0/data.err**2
    returnvec = (data.y - data.cont*newmodel.y)[good]**2 * weights[good] + FittingUtilities.bound(self.resolution_bounds, resolution)
    if self.debug:
      print "Resolution-fitting X^2 = ", numpy.sum(returnvec)/float(good.size), "at R = ", resolution
    if numpy.isnan(numpy.sum(returnvec**2)):
      print "Error! NaN found in ResolutionFitError!"
      outfile=open("ResolutionFitError.log", "a")
      outfile.write("#Error attempting R = %g\n" %(resolution))
      numpy.savetxt(outfile, numpy.transpose((data.x, data.y, data.cont, newmodel.x, newmodel.y)), fmt="%.10g")
      outfile.write("\n\n\n\n")
      numpy.savetxt(outfile, numpy.transpose((model.x, model.y)), fmt="%.10g")
      outfile.write("\n\n\n\n")
      outfile.close()
      raise ValueError
    return returnvec



### -----------------------------------------------  


  
  def Broaden(self, data, model, oversampling = 5, m = 101, dimension = 20, full_output=False):
    """
    Fits the broadening profile using singular value decomposition. This function is
    called by GenerateModel, and is not meant to be called directly!
    
    -oversampling is the oversampling factor to use before doing the SVD
    -m is the size of the broadening function, in oversampled units
    -dimension is the number of eigenvalues to keep in the broadening function. (Keeping too many starts fitting noise)

    -NOTE: This function works well when there are strong telluric lines and a flat continuum.
           If there are weak telluric lines, it's hard to not fit noise.
           If the continuum is not very flat (i.e. from the spectrum of the actual
             object you are trying to telluric correct), the broadening function
             can become multiply-peaked and oscillatory. Use with care!
  """
    n = data.x.size*oversampling
    
    #n must be even, and m must be odd!
    if n%2 != 0:
      n += 1
    if m%2 == 0:
      m += 1
  
    #resample data
    Spectrum = UnivariateSpline(data.x, data.y/data.cont, s=0)
    Model = UnivariateSpline(model.x, model.y, s=0)
    xnew = numpy.linspace(data.x[0], data.x[-1], n)
    ynew = Spectrum(xnew)
    model_new = FittingUtilities.RebinData(model, xnew).y

    #Make 'design matrix'
    design = numpy.zeros((n-m,m))
    for j in range(m):
      for i in range(m/2,n-m/2-1):
        design[i-m/2,j] = model_new[i-j+m/2]
    design = mat(design)
    
    #Do Singular Value Decomposition
    try:
      U,W,V_t = svd(design, full_matrices=False)
    except numpy.linalg.linalg.LinAlgError:
      outfilename = "SVD_Error.log"
      outfile = open(outfilename, "a")
      numpy.savetxt(outfile, numpy.transpose((data.x, data.y, data.cont)))
      outfile.write("\n\n\n\n\n")
      numpy.savetxt(outfile, numpy.transpose((model.x, model.y, model.cont)))
      outfile.write("\n\n\n\n\n")
      outfile.close()
      sys.exit("SVD did not converge! Outputting data to %s" %outfilename)
      
    #Invert matrices:
    #   U, V are orthonormal, so inversion is just their transposes
    #   W is a diagonal matrix, so its inverse is 1/W
    W1 = 1.0/W
    U_t = numpy.transpose(U)
    V = numpy.transpose(V_t)
  
    #Remove the smaller values of W
    W1[dimension:] = 0
    W2 = diagsvd(W1,m,m)
    
    #Solve for the broadening function
    spec = numpy.transpose(mat(ynew[m/2:n-m/2-1]))
    temp = numpy.dot(U_t, spec)
    temp = numpy.dot(W2,temp)
    Broadening = numpy.dot(V,temp)
    
    #Make Broadening function a 1d array
    spacing = xnew[2] - xnew[1]
    xnew = numpy.arange(model.x[0], model.x[-1], spacing)
    model_new = Model(xnew)
    Broadening = numpy.array(Broadening)[...,0]
    
    #Ensure that the broadening function is appropriate:
    maxindex = Broadening.argmax()
    if maxindex > m/2.0 + m/10.0 or maxindex < m/2.0 - m/10.0:
      #The maximum should be in the middle because we already did wavelength calibration!
      outfilename = "SVD_Error2.log"
      numpy.savetxt(outfilename, numpy.transpose((Broadening, )) )
      print "Warning! SVD Broadening function peaked at the wrong location! See SVD_Error2.log for the broadening function"
      
      idx = self.parnames.index("resolution")
      resolution = self.const_pars[idx]
      model = FittingUtilities.ReduceResolution(model, resolution)
      
      #Make broadening function from the gaussian
      centralwavelength = (data.x[0] + data.x[-1])/2.0
      FWHM = centralwavelength/resolution;
      sigma = FWHM/(2.0*numpy.sqrt(2.0*numpy.log(2.0)))
      left = 0
      right = numpy.searchsorted(xnew, 10*sigma)
      x = numpy.arange(0,10*sigma, xnew[1] - xnew[0])
      gaussian = numpy.exp(-(x-5*sigma)**2/(2*sigma**2))
      return FittingUtilities.RebinData(model, data.x), [gaussian/gaussian.sum(), xnew]
      
    elif numpy.mean(Broadening[maxindex-int(m/10.0):maxindex+int(m/10.0)]) < 3* numpy.mean(Broadening[int(m/5.0):]):
      outfilename = "SVD_Error2.log"
      numpy.savetxt(outfilename, numpy.transpose((Broadening, )) )
      print "Warning! SVD Broadening function is not strongly peaked! See SVD_Error2.log for the broadening function"
      
      idx = self.parnames.index("resolution")
      resolution = self.const_pars[idx]
      model = FittingUtilities.ReduceResolution(model, resolution)
      
      #Make broadening function from the gaussian
      centralwavelength = (data.x[0] + data.x[-1])/2.0
      FWHM = centralwavelength/resolution;
      sigma = FWHM/(2.0*numpy.sqrt(2.0*numpy.log(2.0)))
      left = 0
      right = numpy.searchsorted(xnew, 10*sigma)
      x = numpy.arange(0,10*sigma, xnew[1] - xnew[0])
      gaussian = numpy.exp(-(x-5*sigma)**2/(2*sigma**2))
      return FittingUtilities.RebinData(model, data.x), [gaussian/gaussian.sum(), xnew]
    
    #If we get here, the broadening function looks okay.
    #Convolve the model with the broadening function
    model = DataStructures.xypoint(x=xnew)
    Broadened = UnivariateSpline(xnew, numpy.convolve(model_new,Broadening, mode="same"),s=0)
    model.y = Broadened(model.x)
    
    #Fit the broadening function to a gaussian
    params = [0.0, -Broadening[maxindex], maxindex, 10.0]
    params,success = leastsq(self.GaussianErrorFunction, params, args=(numpy.arange(Broadening.size), Broadening))
    sigma = params[3] * (xnew[1] - xnew[0]) 
    FWHM = sigma * 2.0*numpy.sqrt(2.0*numpy.log(2.0))
    resolution = numpy.median(data.x) / FWHM
    #idx = self.parnames.index("resolution")
    #self.const_pars[idx] = resolution
    
    print "Approximate resolution = %g" %resolution
    
    #x2 = numpy.arange(Broadening.size)

    if full_output:
      return FittingUtilities.RebinData(model, data.x), [Broadening, xnew]
    else:
      return FittingUtilities.RebinData(model, data.x)

