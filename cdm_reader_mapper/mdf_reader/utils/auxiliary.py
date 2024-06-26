"""Auxiliary functions and class for reading, converting, decoding and validating MDF files."""

from __future__ import annotations

import csv
import json
import logging
import os
from copy import deepcopy
from io import StringIO

import numpy as np
import pandas as pd
import xarray as xr

from cdm_reader_mapper.common import pandas_TextParser_hdlr
from cdm_reader_mapper.common.getting_files import get_files

from .. import properties
from ..schema import schemas
from ..validate import validate
from . import converters, decoders


def convert_float_format(out_dtypes):
    """DOCUMENTATION."""
    out_dtypes_ = {}
    for k, v in out_dtypes.items():
        if v is None:
            pass
        elif "float" in v:
            v = "float"
        out_dtypes_[k] = v
    return out_dtypes_


def convert_dtypes(dtypes):
    """DOCUMENTATION."""
    dtypes = convert_float_format(dtypes)
    parse_dates = []
    for i, element in enumerate(list(dtypes)):
        if dtypes[element] == "datetime":
            parse_dates.append(element)
            dtypes[element] = "object"
    return dtypes, parse_dates


def validate_arg(arg_name, arg_value, arg_type):
    """Validate input argument is as expected type.

    Parameters
    ----------
    arg_name : str
        Name of the argument
    arg_value : arg_type
        Value of the argument
    arg_type : type
        Type of the argument

    Returns
    -------
    boolean:
        Returns True if type of `arg_value` equals `arg_type`
    """
    if arg_value and not isinstance(arg_value, arg_type):
        logging.error(
            f"Argument {arg_name} must be {arg_type}, input type is {type(arg_value)}"
        )
        return False
    return True


def validate_path(arg_name, arg_value):
    """Validate input argument is an existing directory.

    Parameters
    ----------
    arg_name : str
        Name of the argument
    arg_value : str
        Value of the argument

    Returns
    -------
    boolean
        Returns True if `arg_name` is an existing directory.
    """
    if arg_value and not os.path.isdir(arg_value):
        logging.error(f"{arg_name} could not find path {arg_value}")
        return False
    return True


class Configurator:
    """Class for configuring MDF reader information."""

    def __init__(
        self,
        df=pd.DataFrame(),
        schema={},
        order=[],
        valid=[],
    ):
        self.df = df
        self.orders = order
        self.valid = valid
        self.schema = schema
        self.str_line = ""
        if isinstance(df, pd.Series) or isinstance(df, pd.DataFrame):
            if len(df) > 0:
                self.str_line = df.iloc[0]

    def _add_field_length(self, index):
        if "field_length" in self.sections_dict.keys():
            field_length = self.sections_dict["field_length"]
        else:
            field_length = properties.MAX_FULL_REPORT_WIDTH
        return index + field_length

    def _validate_sentinal(self, i):
        slen = len(self.sentinal)
        str_start = self.str_line[i : i + slen]
        if str_start != self.sentinal:
            self.length = 0
            return i
        else:
            self.sentinal = None
            return self._add_field_length(i)

    def _validate_delimited(self, i, j):
        i = self._skip_delimiter(self.str_line, i)
        if self.delimiter_format == "delimited":
            j = self._next_delimiter(self.str_line, i)
            return i, j
        elif self.field_layout == "fixed_width":
            j = self._add_field_length(i)
            return i, j
        return None, None

    def _skip_delimiter(self, line, index):
        length = len(line)
        while True:
            if index == length:
                break
            if line[index] == self.delimiter:
                index += 1
            break
        return index

    def _next_delimiter(self, line, index):
        while True:
            if index == len(line):
                break
            if line[index] == self.delimiter:
                break
            index += 1
        return index

    def _get_index(self, section):
        if len(self.orders) == 1:
            return section
        else:
            return (self.order, section)

    def _get_ignore(self):
        if self.order in self.valid:
            ignore = self.sections_dict.get("ignore")
        else:
            ignore = True
        if isinstance(ignore, str):
            ignore = eval(ignore)
        return ignore

    def _get_borders(self, i, j):
        if self.sentinal is not None:
            j = self._validate_sentinal(i)
        elif self.delimiter is None:
            j = self._add_field_length(i)
        else:
            i, j = self._validate_delimited(i, j)
            self.missing = False
        return i, j

    def _adjust_right_borders(self, j, k):
        if self.length is None:
            self.length = j - k
        if j - k > self.length:
            self.missing = False
            j = k + self.length
        return j, k

    def _get_dtypes(self):
        return properties.pandas_dtypes.get(self.sections_dict.get("column_type"))

    def _get_converters(self):
        return converters.get(self.sections_dict.get("column_type"))

    def _get_conv_kwargs(self):
        column_type = self.sections_dict.get("column_type")
        if column_type is None:
            return {}
        return {
            converter_arg: self.sections_dict.get(converter_arg)
            for converter_arg in properties.data_type_conversion_args.get(column_type)
        }

    def _get_decoders(self):
        return decoders.get(self.sections_dict["encoding"]).get(
            self.sections_dict.get("column_type")
        )

    def _convert_entries(self, series, converter_func, **kwargs):
        return converter_func(series, **kwargs)

    def _decode_entries(self, series, decoder_func):
        return decoder_func(series)

    def get_configuration(self):
        """Get ICOADS data model specific information."""
        disable_reads = []
        dtypes = {}
        convert = {}
        kwargs = {}
        decode = {}
        for order in self.orders:
            self.order = order
            header = self.schema["sections"][order]["header"]
            disable_read = header.get("disable_read")
            if disable_read is True:
                disable_reads.append(order)
                continue
            sections = self.schema["sections"][order]["elements"]
            for section in sections.keys():
                self.sections_dict = sections[section]
                encoding = sections[section].get("encoding")
                index = self._get_index(section)
                ignore = self._get_ignore()
                if ignore is not True:
                    dtype = self._get_dtypes()
                    if dtype:
                        dtypes[index] = dtype
                    converters = self._get_converters()
                    if converters:
                        convert[index] = converters
                    conv_kwargs = self._get_conv_kwargs()
                    if conv_kwargs:
                        kwargs[index] = conv_kwargs
                    if encoding is not None:
                        decode[index] = self._get_decoders()

        dtypes, parse_dates = convert_dtypes(dtypes)
        return {
            "convert_decode": {
                "converter_dict": convert,
                "converter_kwargs": kwargs,
                "decoder_dict": decode,
                "dtype": dtypes,
            },
            "self": {
                "dtypes": dtypes,
                "disable_reads": disable_reads,
                "parse_dates": parse_dates,
            },
        }

    def open_pandas(self):
        """Open TextParser to pd.DataSeries."""
        missing_values = []
        self.delimiter = None
        i = 0
        j = 0
        data_dict = {}
        for order in self.orders:
            self.order = order
            header = self.schema["sections"][order]["header"]
            self.sentinal = header.get("sentinal")
            self.sentinal_length = header.get("sentinal_length")
            self.delimiter = header.get("delimiter")
            self.field_layout = header.get("field_layout")
            self.delimiter_format = header.get("format")
            disable_read = header.get("disable_read")
            if disable_read is True:
                data_dict[order] = self.str_line[i : properties.MAX_FULL_REPORT_WIDTH]
                continue
            sections = self.schema["sections"][order]["elements"]
            k = i
            for section in sections.keys():
                self.length = header.get("length")
                self.missing = True
                self.sections_dict = sections[section]
                index = self._get_index(section)
                ignore = self._get_ignore()
                na_value = sections[section].get("missing_value")

                i, j = self._get_borders(i, j)

                if i is None:
                    logging.error(
                        f"Delimiter is set to {self.delimiter}. Please specify either format or field_layout in your header schema {header}."
                    )
                    return

                j, k = self._adjust_right_borders(j, k)

                if ignore is not True:
                    data_dict[index] = self.str_line[i:j]

                    if not data_dict[index].strip():
                        data_dict[index] = None
                    if data_dict[index] == na_value:
                        data_dict[index] = None

                if i == j and self.missing is True:
                    missing_values.append(index)

                i = j

        df = pd.Series(data_dict)
        df["missing_values"] = missing_values
        return df

    def open_netcdf(self):
        """Open netCDF to pd.Series."""
        missing_values = []
        attrs = {}
        renames = {}
        disables = []
        for order in self.orders:
            self.order = order
            header = self.schema["sections"][order]["header"]
            disable_read = header.get("disable_read")
            if disable_read is True:
                disables.append(order)
                continue
            sections = self.schema["sections"][order]["elements"]
            for section in sections.keys():
                self.sections_dict = sections[section]
                index = self._get_index(section)
                ignore = self._get_ignore()
                if ignore is not True:
                    if section in self.df.data_vars:
                        renames[section] = index
                    elif section in self.df.dims:
                        renames[section] = index
                    elif section in self.df.attrs:
                        attrs[index] = self.df.attrs[index]
                    else:
                        missing_values.append(index)

        df = self.df[renames.keys()].to_dataframe().reset_index()
        attrs = {k: v.replace("\n", "; ") for k, v in attrs.items()}
        df = df.rename(columns=renames)
        df = df.assign(**attrs)
        for column in disables:
            df[column] = np.nan
        df["missings_value"] = [missing_values] * len(df)
        return df


class _FileReader:
    def __init__(
        self,
        source,
        data_model=None,
        data_model_path=None,
    ):
        # 0. VALIDATE INPUT
        if not data_model and not data_model_path:
            logging.error(
                "A valid data model name or path to data model must be provided"
            )
            return
        if not os.path.isfile(source):
            logging.error(f"Can't find input data file {source}")
            return
        if not validate_path("data_model_path", data_model_path):
            return

        self.source = source
        self.data_model = data_model

        # 1. GET DATA MODEL
        # Schema reader will return empty if cannot read schema or is not valid
        # and will log the corresponding error
        # multiple_reports_per_line error also while reading schema
        if self.data_model:
            model_path = f"{properties._base}.code_tables.{self.data_model}"
            self.code_tables_path = get_files(model_path)
            self.imodel = data_model
            logging.info("READING DATA MODEL SCHEMA FILE...")
            self.schema = schemas.read_schema(schema_name=data_model)
        else:
            self.code_tables_path = os.path.join(data_model_path, "code_tables")
            self.imodel = data_model_path
            logging.info("READING DATA MODEL SCHEMA FILE...")
            self.schema = schemas.read_schema(ext_schema_path=data_model_path)

    def _adjust_dtype(self, dtype, df):
        if not isinstance(dtype, dict):
            return dtype
        return {k: v for k, v in dtype.items() if k in df.columns}

    def _convert_entries(self, series, converter_func, **kwargs):
        return converter_func(series, **kwargs)

    def _decode_entries(self, series, decoder_func):
        return decoder_func(series)

    def _adjust_schema(self, ds, dtypes):
        sections = deepcopy(self.schema["sections"])
        for section in sections.keys():
            elements = sections[section]["elements"]
            for data_var in elements.keys():
                not_in_data_vars = data_var not in ds.data_vars
                not_in_glb_attrs = data_var not in ds.attrs
                not_in_data_dims = data_var not in ds.dims
                if not_in_data_vars and not_in_glb_attrs and not_in_data_dims:
                    del self.schema["sections"][section]["elements"][data_var]
                    continue
                for attr, value in elements[data_var].items():
                    if value == "__from_file__":
                        if attr in ds[data_var].attrs:
                            self.schema["sections"][section]["elements"][data_var][
                                attr
                            ] = ds[data_var].attrs[attr]
                        else:
                            del self.schema["sections"][section]["elements"][data_var][
                                attr
                            ]

    def _get_configurations(self, order, valid):
        config_dict = Configurator(
            schema=self.schema, order=order, valid=valid
        ).get_configuration()
        for attr, val in config_dict["self"].items():
            setattr(self, attr, val)
        del config_dict["self"]
        return config_dict

    def _set_missing_values(self, df, ref):
        explode_ = df.explode("missing_values")
        explode_["index"] = explode_.index
        explode_["values"] = True
        pivots_ = explode_.pivot_table(
            columns="missing_values",
            index="index",
            values="values",
        )
        missing_values = pd.DataFrame(data=pivots_, columns=ref.columns, index=ref.index)
        return missing_values.notna()

    def _read_pandas(self, **kwargs):
        return pd.read_fwf(
            self.source,
            header=None,
            quotechar="\0",
            escapechar="\0",
            dtype=object,
            skip_blank_lines=False,
            **kwargs,
        )

    def _read_netcdf(self, **kwargs):
        ds = xr.open_mfdataset(self.source, **kwargs)
        self._adjust_schema(ds, ds.dtypes)
        return ds.squeeze()

    def _read_sections(
        self,
        TextParser,
        order,
        valid,
        open_with,
    ):
        if open_with == "pandas":
            df = TextParser.apply(
                lambda x: Configurator(
                    df=x, schema=self.schema, order=order, valid=valid
                ).open_pandas(),
                axis=1,
            )
        elif open_with == "netcdf":
            df = Configurator(
                df=TextParser, schema=self.schema, order=order, valid=valid
            ).open_netcdf()
        else:
            raise ValueError("open_with has to be one of ['pandas', 'netcdf']")

        missing_values_ = df["missing_values"]
        del df["missing_values"]
        missing_values = self._set_missing_values(pd.DataFrame(missing_values_), df)
        self.columns = df.columns
        df = df.where(df.notnull(), np.nan)
        return df, missing_values

    def _open_data(
        self,
        order,
        valid,
        chunksize,
        open_with="pandas",
    ):
        if open_with == "netcdf":
            TextParser = self._read_netcdf()
        elif open_with == "pandas":
            TextParser = self._read_pandas(
                encoding=self.schema["header"].get("encoding"),
                widths=[properties.MAX_FULL_REPORT_WIDTH],
                skiprows=self.skiprows,
                chunksize=chunksize,
            )
        else:
            raise ValueError("open_with has to be one of ['pandas', 'netcdf']")

        if isinstance(TextParser, pd.DataFrame) or isinstance(TextParser, xr.Dataset):
            df, self.missing_values = self._read_sections(
                TextParser, order, valid, open_with=open_with
            )
            return df, df.isna()
        else:
            data_buffer = StringIO()
            missings_buffer = StringIO()
            isna_buffer = StringIO()
            for i, df_ in enumerate(TextParser):
                df, missing_values = self._read_sections(
                    df_, order, valid, open_with=open_with
                )
                df_isna = df.isna()
                missing_values.to_csv(
                    missings_buffer,
                    header=False,
                    mode="a",
                    encoding="utf-8",
                    index=False,
                )
                df_isna.to_csv(
                    isna_buffer,
                    header=False,
                    mode="a",
                    index=False,
                    quoting=csv.QUOTE_NONE,
                    sep=properties.internal_delimiter,
                    quotechar="\0",
                    escapechar="\0",
                )
                df.to_csv(
                    data_buffer,
                    header=False,
                    mode="a",
                    encoding="utf-8",
                    index=False,
                    quoting=csv.QUOTE_NONE,
                    sep=properties.internal_delimiter,
                    quotechar="\0",
                    escapechar="\0",
                )
            missings_buffer.seek(0)
            self.missing_values = pd.read_csv(
                missings_buffer,
                names=missing_values.columns,
                chunksize=None,
            )
            data_buffer.seek(0)
            data = pd.read_csv(
                data_buffer,
                names=df.columns,
                chunksize=self.chunksize,
                dtype=object,
                parse_dates=self.parse_dates,
                delimiter=properties.internal_delimiter,
                quotechar="\0",
                escapechar="\0",
            )
            isna_buffer.seek(0)
            isna = pd.read_csv(
                isna_buffer,
                names=df.columns,
                chunksize=self.chunksize,
                delimiter=properties.internal_delimiter,
                quotechar="\0",
                escapechar="\0",
            )
            return data, isna

    def _convert_and_decode_df(
        self,
        df,
        converter_dict,
        converter_kwargs,
        decoder_dict,
    ):
        for section in converter_dict.keys():
            if section not in df.columns:
                continue
            if section in decoder_dict.keys():
                decoded = self._decode_entries(
                    df[section],
                    decoder_dict[section],
                )
                decoded.index = df[section].index
                df[section] = decoded

            converted = self._convert_entries(
                df[section],
                converter_dict[section],
                **converter_kwargs[section],
            )
            converted.index = df[section].index
            df[section] = converted
        return df

    def _create_mask(self, df, isna, missing_values=[]):
        if isna is None:
            isna = df.isna()
        valid = df.notna()
        mask = isna | valid
        if len(missing_values) > 0:
            mask[missing_values] = False
        return mask

    def _validate_df(self, df, isna=None):
        mask = self._create_mask(df, isna, missing_values=self.missing_values)
        return validate(
            df,
            mask,
            self.schema,
            self.code_tables_path,
            disables=self.disable_reads,
        )

    def _dump_atts(self, out_atts, out_path):
        """Dump attributes to atts.json."""
        if not isinstance(self.data, pd.io.parsers.TextFileReader):
            data = [self.data]
            valid = [self.mask]
        else:
            data = pandas_TextParser_hdlr.make_copy(self.data)
            valid = pandas_TextParser_hdlr.make_copy(self.mask)
        logging.info(f"WRITING DATA TO FILES IN: {out_path}")
        for i, (data_df, valid_df) in enumerate(zip(data, valid)):
            header = False
            mode = "a"
            out_atts_json = {}
            if i == 0:
                mode = "w"
                cols = [x for x in data_df]
                if isinstance(cols[0], tuple):
                    header = [":".join(x) for x in cols]
                    out_atts_json = {
                        ":".join(x): out_atts.get(x) for x in out_atts.keys()
                    }
                else:
                    header = cols
                    out_atts_json = out_atts
            kwargs = {
                "header": header,
                "mode": mode,
                "encoding": "utf-8",
                "index": True,
                "index_label": "index",
                "escapechar": "\0",
            }
            data_df.to_csv(os.path.join(out_path, "data.csv"), **kwargs)
            valid_df.to_csv(os.path.join(out_path, "mask.csv"), **kwargs)

            with open(os.path.join(out_path, "atts.json"), "w") as fileObj:
                json.dump(out_atts_json, fileObj, indent=4)
